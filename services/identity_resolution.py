"""Explainable identity evidence, without scores or decisions about students.

Fuzzy candidates are blocked by sorted-name first letter, year and programme,
then by an actual corroborator. Work is O(n + sum(k_b ** 2)) in corroborated
blocks, never a global all-records Cartesian product. A dense corroborated
block can still be quadratic; all its qualifying pairs must be considered.

scan() owns its unit of work: callers must commit ingestion before calling it.
The existing audit_service.log commits evidence and audit together. Stable
scope/row IDs and evidence metadata provide scoped idempotency on A1's schema.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from importlib import import_module
from itertools import combinations
import hmac
import json
import math
import re
import unicodedata
from typing import Any, Callable, Iterable
from uuid import NAMESPACE_URL, uuid4, uuid5

from flask import current_app, has_request_context
from flask_login import current_user
from rapidfuzz import fuzz
from sqlalchemy import or_, select

from extensions import db
from models import AuditLog, DataSubmission, IdentityGraphEdge, IdentityMatch
from models import Institution, Programme, StudentRecord, SubmissionError, SystemPolicy
from services import audit_service


def _optional(module: str, name: str) -> Callable | None:
    """Use A's implementation when present; never hide its dependency failures."""
    try:
        return getattr(import_module(module), name, None)
    except ModuleNotFoundError as exc:
        if exc.name != module:
            raise
        return None


def normalize_name(value: str | None) -> str:
    """Reuse A6 when available; otherwise normalize Unicode, case and spacing."""
    shared = _optional("services.validation_service", "normalize_name")
    if shared:
        return shared(value or "")
    text = unicodedata.normalize("NFKD", value or "").casefold()
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join("".join(char if char.isalnum() else " " for char in text).split())


def normalize_contact(value: str | None) -> str:
    """Normalize synthetic phone/email/contact references without inventing IDs."""
    normalized = unicodedata.normalize("NFKC", value or "").strip().casefold()
    if "@" in normalized:
        return normalized  # Email punctuation is significant; do not merge distinct addresses.
    return "".join(char for char in normalized if char.isalnum())


def _mask(ref: str | None) -> str:
    """Mask even malformed short references; no raw identifier enters evidence."""
    shared = _optional("services.apaar_adapter", "mask_apaar")
    if shared:
        return shared(ref or "")
    value = (ref or "").strip()
    return f"XXXXXXXX{value[-4:]}" if len(value) > 4 else "XXXXXXXX"


@dataclass(frozen=True)
class StudentSnapshot:
    """Immutable detector input; raw references are internal and excluded from repr."""

    id: str
    institution_id: str
    institution_student_id: str
    academic_year: str
    programme_id: str
    normalized_name: str
    admission_date: str
    enrolment_status: str = "ACTIVE"
    apaar_ref: str = field(default="", repr=False)
    contact_ref: str = field(default="", repr=False)
    fee_payer_ref: str = field(default="", repr=False)
    programme_key: str = ""
    source_submission_id: str | None = None


@dataclass(frozen=True)
class DetectedEvent:
    """Evidence for human review, not a finding of fraud or a risk score."""

    event_key: str
    rule_key: str
    student_ids: list[str]
    evidence: dict[str, Any]
    explanation: str
    is_signal: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Serialize only already-redacted event fields."""
        return asdict(self)


@dataclass
class ScanResult:
    """A scoped evidence snapshot plus unique invocation ID and UTC timestamps."""

    scan_id: str
    matches: list[dict]
    edges: list[dict]
    events: list[DetectedEvent]
    stats: dict
    started_at: datetime
    finished_at: datetime
    nodes: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Return JSON-ready results, never StudentRecord.to_dict(mask_apaar=False)."""
        result = asdict(self)
        result["started_at"] = self.started_at.isoformat()
        result["finished_at"] = self.finished_at.isoformat()
        return result


def _key(*parts: Any) -> str:
    """Create deterministic opaque keys independent of input iteration order."""
    return sha256(json.dumps(parts, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _event(rule: str, rows: Iterable[StudentSnapshot], evidence: dict, explanation: str,
           signal: bool = False, subject: str | None = None) -> DetectedEvent:
    """Build a stable event using internal record IDs, never raw references."""
    ids = sorted({row.id for row in rows})
    return DetectedEvent(_key(rule, subject or ids), rule, ids, evidence, explanation, signal)


def _groups(rows: Iterable[StudentSnapshot], key: Callable) -> list[list[StudentSnapshot]]:
    """Group non-empty keys deterministically and deduplicate repeated input IDs."""
    groups = defaultdict(dict)
    for row in rows:
        value = key(row)
        if value:
            groups[value][row.id] = row
    return [sorted(group.values(), key=lambda row: row.id) for group in groups.values()]


def _transfer(a: StudentSnapshot, b: StudentSnapshot) -> bool:
    """Highlight withdrawn/active migration candidates rather than asserting fraud."""
    return a.institution_id != b.institution_id and {a.enrolment_status, b.enrolment_status} == {"WITHDRAWN", "ACTIVE"}


def detect_duplicate_apaar(rows: Iterable[StudentSnapshot]) -> list[DetectedEvent]:
    """Find repeated valid references on distinct institutional identities."""
    events = []
    for group in _groups(rows, lambda row: row.apaar_ref):
        for a, b in combinations(group, 2):
            if a.institution_id == b.institution_id and a.institution_student_id == b.institution_student_id:
                continue
            possible_transfer = _transfer(a, b)
            events.append(_event("DUPLICATE_APAAR", [a, b], {
                "apaar_masked": _mask(a.apaar_ref), "possible_transfer": possible_transfer,
            }, "Same synthetic APAAR reference on distinct institutional records; human review required."
                + (" Withdrawn/active statuses may indicate a transfer." if possible_transfer else "")))
    return events


def detect_simultaneous_enrolment(rows: Iterable[StudentSnapshot]) -> list[DetectedEvent]:
    """Find ACTIVE enrolments at different institutions in the same year."""
    events = []
    active = [row for row in rows if row.apaar_ref and row.enrolment_status == "ACTIVE"]
    for group in _groups(active, lambda row: (row.apaar_ref, row.academic_year)):
        for a, b in combinations(group, 2):
            if a.institution_id != b.institution_id:
                events.append(_event("SIMULTANEOUS_ENROLMENT", [a, b], {
                    "apaar_masked": _mask(a.apaar_ref), "academic_year": a.academic_year,
                }, "Conflicting ACTIVE enrolment in two institutions in the same academic year; human review required."))
    return events


def detect_duplicate_institute_id(rows: Iterable[StudentSnapshot]) -> list[DetectedEvent]:
    """Detect incoming ID conflicts, even though A1 prevents persisting such pairs."""
    events = []
    for group in _groups(rows, lambda row: (row.institution_id, row.institution_student_id, row.academic_year)):
        for a, b in combinations(group, 2):
            if a.apaar_ref != b.apaar_ref or a.normalized_name != b.normalized_name:
                events.append(_event("DUPLICATE_INSTITUTE_ID", [a, b], {
                    "institution_id": a.institution_id, "academic_year": a.academic_year,
                    "conflicting_fields": [key for key in ("apaar_ref", "normalized_name") if getattr(a, key) != getattr(b, key)],
                }, "Same institutional student ID and year with conflicting APAAR or name; human review required."))
    return events


def _programme(row: StudentSnapshot) -> str:
    """Use canonical programme codes across institutions, not institution-local UUIDs."""
    return row.programme_key or row.programme_id


def _id_pattern(value: str) -> str:
    """Ignore punctuation/case only; never equate every numeric roll number."""
    return normalize_contact(value)


def _corroborators(a: StudentSnapshot, b: StudentSnapshot) -> list[str]:
    """Return actual non-empty supporting attributes, not name similarity itself."""
    reasons = []
    if _programme(a) == _programme(b) and a.academic_year == b.academic_year and a.admission_date and a.admission_date == b.admission_date:
        reasons.append("same programme, academic year and admission date")
    if a.contact_ref and a.contact_ref == b.contact_ref:
        reasons.append("same normalized contact reference")
    if a.fee_payer_ref and a.fee_payer_ref == b.fee_payer_ref:
        reasons.append("same normalized fee-payer reference")
    if _id_pattern(a.institution_student_id) and _id_pattern(a.institution_student_id) == _id_pattern(b.institution_student_id):
        reasons.append("same institutional student ID pattern")
    return reasons


def detect_similar_names(rows: Iterable[StudentSnapshot], threshold: float = 88,
                         stats: dict | None = None) -> list[DetectedEvent]:
    """Compare only blocked, corroborated pairs using token-sort and WRatio."""
    if not math.isfinite(float(threshold)) or not 0 <= float(threshold) <= 100:
        raise ValueError("Name similarity threshold must be between 0 and 100.")
    blocks = defaultdict(dict)
    for row in rows:
        tokens = sorted(row.normalized_name.split())
        if not tokens:
            continue
        block = (tokens[0][0], row.academic_year, _programme(row))
        attributes = [("date", row.admission_date), ("contact", row.contact_ref),
                      ("payer", row.fee_payer_ref), ("registration", _id_pattern(row.institution_student_id))]
        for kind, value in attributes:
            if value:
                blocks[(*block, kind, value)][row.id] = row
    seen, events = set(), []
    for group in blocks.values():
        for a, b in combinations(sorted(group.values(), key=lambda row: row.id), 2):
            pair = (a.id, b.id)
            if pair in seen:
                continue
            seen.add(pair)
            if stats is not None:
                stats["fuzzy_pairs_compared"] = stats.get("fuzzy_pairs_compared", 0) + 1
            reasons = _corroborators(a, b)
            score = max(fuzz.token_sort_ratio(a.normalized_name, b.normalized_name), fuzz.WRatio(a.normalized_name, b.normalized_name))
            if reasons and score >= float(threshold):
                events.append(_event("SIMILAR_NAME_CORROBORATED", [a, b], {
                    "similarity": round(score, 2), "threshold": float(threshold), "corroboration": reasons,
                    "possible_transfer": _transfer(a, b),
                }, f"Similar name ({score:.0f}%) and {'; '.join(reasons)}; possible identity link requiring human review."))
    return events


def detect_shared_contact(rows: Iterable[StudentSnapshot]) -> list[DetectedEvent]:
    """Create investigation SIGNALS only; never duplicate-identity matches."""
    return [_event("SHARED_CONTACT", group, {"shared_count": len(group), "not_proof": True},
                   f"Same normalized contact reference (shared by {len(group)} records — investigation signal, not proof).", True)
            for group in _groups(rows, lambda row: row.contact_ref) if len(group) > 1]


def detect_shared_fee_payer(rows: Iterable[StudentSnapshot]) -> list[DetectedEvent]:
    """Create payer-sharing SIGNALS only; never duplicate-identity matches."""
    return [_event("SHARED_FEE_PAYER", group, {"shared_count": len(group), "not_proof": True},
                   f"Same normalized fee-payer reference (shared by {len(group)} records — investigation signal, not proof).", True)
            for group in _groups(rows, lambda row: row.fee_payer_ref) if len(group) > 1]


def detect_malformed_apaar(rows: Iterable[StudentSnapshot], validate_format: Callable[[str], bool]) -> list[DetectedEvent]:
    """Describe absent/malformed identifiers without returning their raw values."""
    return [_event("MISSING_OR_MALFORMED_APAAR", [row], {
        "reason": "missing" if not row.apaar_ref else "malformed", "apaar_masked": _mask(row.apaar_ref),
    }, "APAAR reference is missing or malformed; this is a format check only, not government identity verification.")
        for row in rows if not validate_format(row.apaar_ref)]


def detect_conflicting_academic_data(rows: Iterable[StudentSnapshot]) -> list[DetectedEvent]:
    """Compare programmes, admission dates and statuses only within the same year."""
    events = []
    for group in _groups((row for row in rows if row.apaar_ref), lambda row: (row.apaar_ref, row.academic_year)):
        for a, b in combinations(group, 2):
            fields = [name for name, different in [("programme", _programme(a) != _programme(b)),
                ("admission_date", a.admission_date != b.admission_date),
                ("enrolment_status", a.enrolment_status != b.enrolment_status)] if different]
            if fields:
                events.append(_event("CONFLICTING_ACADEMIC_DATA", [a, b], {
                    "academic_year": a.academic_year, "conflicting_fields": fields,
                    "apaar_masked": _mask(a.apaar_ref), "possible_transfer": _transfer(a, b),
                }, "Same synthetic APAAR reference has conflicting " + ", ".join(fields)
                    + " in the same academic year; check corrections and transfer/migration before deciding."))
    return events


def detect_duplicate_submissions(flags: Iterable[dict], rows: Iterable[StudentSnapshot]) -> list[DetectedEvent]:
    """Project explicit A8 duplicate flags; retries are not identity proof."""
    by_submission = defaultdict(list)
    for row in rows:
        by_submission[row.source_submission_id].append(row)
    events = {}
    for flag in flags:
        submission_id = flag["submission_id"]
        event = _event("DUPLICATE_SUBMISSION", by_submission[submission_id], {
            "submission_id": submission_id, "institution_id": flag["institution_id"], "not_proof": True,
        }, "Ingestion marked this submission as a duplicate; repeated upload is an investigation signal, not proof of duplicate identity.", True, submission_id)
        events[event.event_key] = event
    return list(events.values())


def _actor_scope(institution_id: str | None) -> tuple[str | None, str, str, bool]:
    """Enforce a request's institution boundary; app-context jobs are trusted callers."""
    if not has_request_context():
        return institution_id, "SYSTEM", "SYSTEM", institution_id is None
    if not current_user.is_authenticated:
        raise PermissionError("Authentication is required to run an identity scan.")
    role = current_user.role
    if role == "AICTE_ADMIN":
        return institution_id, current_user.username, role, True
    if role == "INSTITUTION_ADMIN" and current_user.institution_id:
        own = current_user.institution_id
        if institution_id is not None and institution_id != own:
            raise PermissionError("Only your own institution can be scanned.")
        return own, current_user.username, role, False
    raise PermissionError("This role cannot run an identity scan.")


def _policy(key: str, fallback: Any) -> Any:
    """Read policy values without changing them."""
    row = db.session.execute(select(SystemPolicy).where(SystemPolicy.key == key)).scalar_one_or_none()
    return row.value if row is not None else fallback


def _validator() -> Callable[[str], bool]:
    """Use A6's adapter or a policy-driven temporary format-only implementation."""
    shared = _optional("services.apaar_adapter", "validate_format")
    if shared:
        def validate(value: str) -> bool:
            """Require an explicit boolean result from the shared adapter."""
            result = shared(value)
            if not isinstance(result, bool):
                raise TypeError("apaar_adapter.validate_format must return a boolean.")
            return result
        return validate
    pattern = _policy("apaar_regex", r"\d{12}")
    try:
        regex = re.compile(pattern)
    except (TypeError, re.error) as exc:
        raise ValueError("The APAAR format policy must be a valid regular expression.") from exc
    return lambda value: bool(value and regex.fullmatch(value))


def _submission_flags(institution_id: str | None, submission_id: str | None,
                      academic_year: str | None) -> list[dict]:
    """Read explicit submission_errors/audit flags, never guess duplicates from hashes."""
    batches = select(DataSubmission.id, DataSubmission.institution_id)
    if institution_id:
        batches = batches.where(DataSubmission.institution_id == institution_id)
    if submission_id:
        batches = batches.where(DataSubmission.id == submission_id)
    if academic_year:
        linked = select(StudentRecord.source_submission_id).where(StudentRecord.academic_year == academic_year)
        if institution_id:
            linked = linked.where(StudentRecord.institution_id == institution_id)
        batches = batches.where(DataSubmission.id.in_(linked))
    batches = dict(db.session.execute(batches).all())
    if not batches:
        return []
    flagged = set(db.session.execute(select(SubmissionError.submission_id).where(
        SubmissionError.submission_id.in_(batches), SubmissionError.code == "DUPLICATE_SUBMISSION")).scalars())
    audit_ids = db.session.execute(select(AuditLog.entity_id).where(
        AuditLog.entity_id.in_(batches),
        or_(AuditLog.action.in_(["DUPLICATE_SUBMISSION", "SUBMISSION_DUPLICATE"]),
            AuditLog.details["rule_key"].as_string() == "DUPLICATE_SUBMISSION",
            AuditLog.details["duplicate_submission"].as_boolean() == True),
    )).scalars()
    flagged.update(audit_ids)
    return [{"submission_id": key, "institution_id": batches[key]} for key in sorted(flagged)]


def _snapshot(row: StudentRecord, programmes: dict[str, Programme]) -> StudentSnapshot:
    """Copy the immutable detector fields without touching a StudentRecord."""
    programme = programmes[row.programme_id]
    return StudentSnapshot(row.id, row.institution_id, row.institution_student_id, row.academic_year,
        row.programme_id, normalize_name(row.normalized_name or row.full_name),
        row.admission_date.isoformat() if row.admission_date else "", row.enrolment_status,
        (row.apaar_ref or "").strip(), normalize_contact(row.contact_ref), normalize_contact(row.fee_payer_ref),
        normalize_name(programme.code), row.source_submission_id)


def _graph(rows: list[StudentSnapshot], events: list[DetectedEvent], records: dict,
           institutions: dict) -> tuple[list[dict], list[dict]]:
    """Build redacted node labels and explained relationships for this view only."""
    nodes, edges = {}, {}
    contact_counts = Counter(row.contact_ref for row in rows if row.contact_ref)
    payer_counts = Counter(row.fee_payer_ref for row in rows if row.fee_payer_ref)

    def opaque(kind: str, value: str) -> str:
        """Use a keyed opaque node ID rather than a recoverable contact/APAAR value."""
        digest = hmac.new(str(current_app.config["SECRET_KEY"]).encode(), f"{kind}:{value}".encode(), "sha256").hexdigest()
        return str(uuid5(NAMESPACE_URL, digest))

    def node(kind: str, id_: str, label: str) -> None:
        """Insert a typed node without storing raw reference values."""
        nodes[(kind, id_)] = {"id": f"{kind.lower()}:{id_}", "entity_id": id_, "type": kind, "label": label}

    def edge(source: str, kind: str, target: str, type_: str, explanation: str, evidence: dict) -> None:
        """Insert a stable explained edge; shared links remain marked as signals."""
        key = _key(source, kind, target, type_, evidence.get("event_key"))
        edges[key] = {"source_type": "STUDENT", "source_id": source, "target_type": kind,
            "target_id": target, "edge_type": type_, "explanation": explanation, "evidence": evidence}

    for row in rows:
        node("STUDENT", row.id, records[row.id].full_name)
        node("INSTITUTION", row.institution_id, institutions[row.institution_id].code)
        edge(row.id, "INSTITUTION", row.institution_id, "ENROLLED_AT", "Student record belongs to this institution in the indicated academic year.", {"academic_year": row.academic_year})
        if row.apaar_ref:
            target = opaque("APAAR", row.apaar_ref)
            node("APAAR", target, _mask(row.apaar_ref))
            edge(row.id, "APAAR", target, "APAAR_REFERENCE", "Student record uses this masked synthetic APAAR reference; format validation is not identity verification.", {"apaar_masked": _mask(row.apaar_ref)})
        for kind, value, counts, rule in [("CONTACT", row.contact_ref, contact_counts, "SHARED_CONTACT"),
                                           ("FEE_PAYER", row.fee_payer_ref, payer_counts, "SHARED_FEE_PAYER")]:
            if value:
                target = opaque(kind, value)
                node(kind, target, "Contact reference (signal)" if kind == "CONTACT" else "Fee-payer reference (signal)")
                explanation = f"Same normalized {kind.lower().replace('_', '-')} reference (shared by {counts[value]} records — investigation signal, not proof)."
                edge(row.id, kind, target, rule, explanation, {"is_signal": True, "not_proof": True, "shared_count": counts[value]})
    for event in events:
        if not event.is_signal and len(event.student_ids) == 2:
            a, b = event.student_ids
            edge(a, "STUDENT", b, event.rule_key, event.explanation,
                 {**event.evidence, "event_key": event.event_key, "is_signal": False})
    return sorted(nodes.values(), key=lambda item: item["id"]), list(edges.values())


def _persist(model: type, desired: list[dict], scope: str, kind: str) -> list[dict]:
    """Upsert this scope's projection and remove its stale rows, preserving other scopes."""
    existing = {row.id: row for row in db.session.execute(select(model).where(
        model.evidence["identity_scope"].as_string() == scope)).scalars()}
    kept = set()
    for values in desired:
        identity = values["event_key"] if kind == "match" else _key(values["source_id"], values["target_type"], values["target_id"], values["edge_type"], values["evidence"].get("event_key"))
        id_ = str(uuid5(NAMESPACE_URL, f"identity:{scope}:{kind}:{identity}"))
        kept.add(id_)
        values = {**values, "evidence": {**values["evidence"], "identity_scope": scope}}
        row = existing.get(id_)
        if row is None:
            row = model(id=id_, **values)
            db.session.add(row)
        else:
            for key, value in values.items():
                setattr(row, key, value)
    for id_, row in existing.items():
        if id_ not in kept:
            db.session.delete(row)
    db.session.flush()
    return [db.session.get(model, id_).to_dict() for id_ in sorted(kept)]


def scan(institution_id: str | None = None, submission_id: str | None = None,
         academic_year: str | None = None) -> ScanResult:
    """Scan committed records, atomically persist evidence+audit, never make decisions.

    Institution admins see/read only their institution. AICTE may compare peers
    across institutions, while returning only conflicts touching the focus.
    Without a request, a scoped system job is local; an unscoped job is global.
    """
    if db.session.new or db.session.dirty or db.session.deleted:
        raise ValueError("Commit pending changes before running an identity scan.")
    institution_id, actor, role, cross_institution = _actor_scope(institution_id)
    for value in (institution_id, submission_id, academic_year):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError("Scan scope values must be non-empty strings.")
    started = datetime.now(timezone.utc)
    scan_id = str(uuid4())
    scope = _key(institution_id, submission_id, academic_year, "cross" if cross_institution else "local")
    try:
        query = select(StudentRecord)
        scope_query = _optional("services.authz", "scope_query")
        if has_request_context() and scope_query:
            query = scope_query(query, StudentRecord)
        if academic_year:
            query = query.where(StudentRecord.academic_year == academic_year)
        if institution_id and not cross_institution:
            query = query.where(StudentRecord.institution_id == institution_id)
        records = {row.id: row for row in db.session.execute(query.order_by(StudentRecord.id)).scalars()}
        anchors = {id_ for id_, row in records.items() if (not institution_id or row.institution_id == institution_id)
                   and (not submission_id or row.source_submission_id == submission_id)}
        programmes = {row.id: row for row in db.session.execute(select(Programme).where(
            Programme.id.in_({row.programme_id for row in records.values()}))).scalars()}
        snapshots = [_snapshot(row, programmes) for row in records.values()]
        validator = _validator()
        valid = [row for row in snapshots if validator(row.apaar_ref)]
        threshold = _policy("identity_name_threshold", current_app.config.get("IDENTITY_NAME_THRESHOLD", 88))
        stats = {"records_read": len(records), "anchor_records": len(anchors), "fuzzy_pairs_compared": 0}
        events = (detect_duplicate_apaar(valid) + detect_simultaneous_enrolment(valid)
                  + detect_duplicate_institute_id(snapshots) + detect_similar_names(snapshots, threshold, stats)
                  + detect_shared_contact(snapshots) + detect_shared_fee_payer(snapshots)
                  + detect_malformed_apaar(snapshots, validator) + detect_conflicting_academic_data(valid))
        events = [event for event in events if anchors.intersection(event.student_ids)]
        flags = _submission_flags(institution_id, submission_id, academic_year)
        events.extend(detect_duplicate_submissions(flags, snapshots))
        events = sorted({event.event_key: event for event in events}.values(), key=lambda event: event.event_key)
        relevant = anchors.union(*(set(event.student_ids) for event in events))
        graph_rows = [row for row in snapshots if row.id in relevant]
        institutions = {row.id: row for row in db.session.execute(select(Institution).where(
            Institution.id.in_({row.institution_id for row in graph_rows}))).scalars()}
        nodes, edge_values = _graph(graph_rows, events, records, institutions)
        match_values = [{"student_a_id": event.student_ids[0], "student_b_id": event.student_ids[1],
            "match_type": event.rule_key, "confidence": event.evidence.get("similarity", 100) / 100,
            "evidence": {**event.evidence, "explanation": event.explanation, "is_signal": False},
            "scan_id": scan_id, "event_key": event.event_key}
            for event in events if not event.is_signal and len(event.student_ids) == 2]
        matches = _persist(IdentityMatch, match_values, scope, "match")
        edges = _persist(IdentityGraphEdge, edge_values, scope, "edge")
        for edge in edges:
            edge["source"] = f"{edge['source_type'].lower()}:{edge['source_id']}"
            edge["target"] = f"{edge['target_type'].lower()}:{edge['target_id']}"
        stats.update(events=len(events), signals=sum(event.is_signal for event in events), matches=len(matches),
                     edges=len(edges), nodes=len(nodes), by_rule=dict(sorted(Counter(event.rule_key for event in events).items())))
        finished = datetime.now(timezone.utc)
        audit = audit_service.log(actor, "IDENTITY_SCAN_FINISHED", "IDENTITY_SCAN", scan_id, {
            "actor_role": role, "scope": {"institution_id": institution_id, "submission_id": submission_id,
            "academic_year": academic_year, "cross_institution": cross_institution}, "scope_key": scope,
            "started_at": started.isoformat(), "finished_at": finished.isoformat(), "counts": stats,
        }, institution_id=institution_id)
        if not audit.get("id"):
            raise RuntimeError("Identity scan could not persist its audit record.")
        return ScanResult(scan_id, matches, edges, events, stats, started, finished, nodes)
    except Exception:
        db.session.rollback()
        raise
