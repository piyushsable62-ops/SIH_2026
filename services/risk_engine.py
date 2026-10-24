"""Deterministic evidence for human review, never a finding of fraud.

This module NEVER changes students, classifies them as fraudulent, revokes a
credential or takes a punitive action. Callers commit ingestion/admission work
first: the current audit service commits this service's unit of work.
"""

from collections import defaultdict
from dataclasses import asdict, is_dataclass
from hashlib import sha256
import json
import re
import secrets
from typing import Any, Iterable
from uuid import UUID, uuid4

from flask import current_app, has_request_context
from flask_login import current_user
from sqlalchemy import select

from extensions import db
from models import AdmissionEvent, CaseAction, Institution, RiskCase, RiskEvidence
from models import StudentRecord, SystemPolicy
from services import audit_service

DEFAULT_WEIGHTS = {
    "DUPLICATE_APAAR": 40, "SIMULTANEOUS_ENROLMENT": 35,
    "DUPLICATE_INSTITUTE_ID": 25, "SIMILAR_NAME_CORROBORATED": 15,
    "SHARED_CONTACT": 10, "SHARED_FEE_PAYER": 10,
    "MISSING_OR_MALFORMED_APAAR": 20, "CONFLICTING_ACADEMIC_DATA": 15,
    "EXCESS_INTAKE_VIOLATION": 30,
}
DEFAULT_BANDS = {"LOW": [0, 29], "MEDIUM": [30, 59], "HIGH": [60, 79], "CRITICAL": [80, 100]}
SIGNAL_RULES = {"SHARED_CONTACT", "SHARED_FEE_PAYER", "DUPLICATE_SUBMISSION"}
ENGINE = "risk-v1"
LABELS = {"DUPLICATE_APAAR": "duplicate APAAR", "SIMULTANEOUS_ENROLMENT": "simultaneous enrolment"}


def _clean(value: Any) -> Any:
    """Redact raw references/secrets and full 12-digit identifiers defensively."""
    if isinstance(value, dict):
        return {str(key): ("[REDACTED]" if any(part in str(key).lower() for part in
                ("password", "token", "secret", "private_key", "contact_ref", "fee_payer_ref", "apaar_ref"))
                else _clean(item)) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_clean(item) for item in value]
    if isinstance(value, str):
        try:
            UUID(value)
            return value  # Numeric UUID segments are internal keys, not APAAR references.
        except ValueError:
            pass
        return re.sub(r"(?<!\d)\d{12}(?!\d)", lambda match: "XXXXXXXX" + match[0][-4:], value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    raise ValueError("Evidence details must be JSON-compatible.")


def _event(value: Any) -> dict:
    """Normalize B2 dataclasses or dictionaries into a validated evidence row."""
    value = asdict(value) if is_dataclass(value) else dict(value)
    key, rule = value.get("event_key"), value.get("rule_key")
    if not isinstance(key, str) or not key.strip() or len(key) > 100:
        raise ValueError("An event requires a stable event_key of at most 100 characters.")
    if not isinstance(rule, str) or not rule.strip() or len(rule) > 50:
        raise ValueError("An event requires a rule_key of at most 50 characters.")
    ids = value.get("student_ids", value.get("related_student_ids", []))
    if not isinstance(ids, (list, tuple)) or any(not isinstance(id_, str) or not id_ for id_ in ids):
        raise ValueError("Student IDs must be non-empty strings.")
    details = value.get("evidence", value.get("details", {})) or {}
    if not isinstance(details, dict):
        raise ValueError("Event evidence must be an object.")
    description = value.get("explanation", value.get("description"))
    if not isinstance(description, str) or not description.strip():
        raise ValueError("Every event needs a human-readable explanation.")
    return {"event_key": key, "rule_key": rule, "student_ids": sorted(set(ids)),
            "description": _clean(description), "details": _clean(details),
            "is_signal": rule in SIGNAL_RULES or bool(value.get("is_signal", details.get("is_signal", False)))}


def _weights(weights: dict) -> dict:
    """Validate non-negative integer points; unknown rules default to zero."""
    if not isinstance(weights, dict) or any(not isinstance(key, str) or
            isinstance(value, bool) or not isinstance(value, int) or value < 0 for key, value in weights.items()):
        raise ValueError("Risk weights must map rule keys to non-negative integer points.")
    return weights


def _bands(bands: dict) -> dict:
    """Require ordered, contiguous, inclusive LOW through CRITICAL ranges."""
    if not isinstance(bands, dict) or set(bands) != set(DEFAULT_BANDS):
        raise ValueError("Risk bands must define LOW, MEDIUM, HIGH and CRITICAL ranges.")
    next_start = 0
    for name in DEFAULT_BANDS:
        limits = bands[name]
        if (not isinstance(limits, (list, tuple)) or len(limits) != 2 or
                any(isinstance(item, bool) or not isinstance(item, int) for item in limits) or
                limits[0] != next_start or limits[1] < limits[0] or limits[1] > 100):
            raise ValueError("Risk bands must partition the integers 0 through 100 in severity order.")
        next_start = limits[1] + 1
    if next_start != 101:
        raise ValueError("Risk bands must cover 0 through 100.")
    return bands


def _distinct(events: Iterable, weights: dict) -> list[dict]:
    """Choose one rule per key, highest weight first with a stable lexical tie-break."""
    choices = defaultdict(list)
    for value in events:
        event = _event(value)
        choices[event["event_key"]].append(event)
    return [sorted(choices[key], key=lambda event: (
        -weights.get(event["rule_key"], 0), event["rule_key"], json.dumps(event, sort_keys=True)))[0]
        for key in sorted(choices)]


def _score(events: Iterable, weights: dict, bands: dict) -> tuple[int, str, list[dict]]:
    """Score distinct events with a signal safety ceiling independent of policy."""
    weights, bands = _weights(weights), _bands(bands)
    events = _distinct(events, weights)
    breakdown = [{**event, "points": weights.get(event["rule_key"], 0)} for event in events]
    signal_only = bool(events) and all(event["is_signal"] for event in events)
    ceiling = min(29, bands["LOW"][1]) if signal_only else 100
    score = min(ceiling, sum(item["points"] for item in breakdown))
    severity = next(name for name, (low, high) in bands.items() if low <= score <= high)
    return score, severity, breakdown


def score_events(events: Iterable, weights: dict) -> tuple[int, str, list[dict]]:
    """Pure scoring with standard bands; no Flask, database or side effects.

    Distinct event keys score once, even across rules. Breakdown points are raw
    contributions; the final total is capped at 100 (signal-only totals at LOW).
    """
    return _score(events, weights, DEFAULT_BANDS)


def _policies() -> tuple[dict, dict]:
    """Load configurable weights and bands, warning on missing weights."""
    policies = {row.key: row.value for row in db.session.execute(select(SystemPolicy).where(
        SystemPolicy.key.in_(["risk_weights", "risk_bands"]))).scalars()}
    if "risk_weights" not in policies:
        current_app.logger.warning("risk_weights policy missing; using AGENTS.md defaults.")
    configured = _weights(policies.get("risk_weights", DEFAULT_WEIGHTS))
    return {**DEFAULT_WEIGHTS, **configured}, _bands(policies.get("risk_bands", DEFAULT_BANDS))


def _actor() -> tuple[str, str, str | None]:
    """Enforce request roles; app-context jobs are trusted internal callers."""
    if not has_request_context():
        return "SYSTEM", "SYSTEM", None
    if not current_user.is_authenticated:
        raise PermissionError("Authentication is required to evaluate risk.")
    if current_user.role == "AICTE_ADMIN":
        return current_user.username, current_user.role, None
    if current_user.role == "INSTITUTION_ADMIN" and current_user.institution_id:
        return current_user.username, current_user.role, current_user.institution_id
    raise PermissionError("This role cannot evaluate risk.")


def _ready() -> None:
    """Reject unrelated pending work before an audit-owned commit."""
    if db.session.new or db.session.dirty or db.session.deleted:
        raise ValueError("Commit pending changes before evaluating risk.")


def _cluster_key(kind: str, subjects: list[str]) -> str:
    """Derive a stable key from sorted internal subject IDs."""
    return sha256(json.dumps([kind, sorted(subjects)], separators=(",", ":")).encode()).hexdigest()


def _clusters(events: list[dict]) -> list[tuple[str, list[dict]]]:
    """Union only substantive links; signals can never bridge identity clusters.

    Signals wholly inside one identity component attach there. Other signals
    form separate investigation components, even when a member also has a case.
    Each event is allocated exactly once.
    """
    parent = {}

    def root(id_: str) -> str:
        """Find and compress the deterministic union-find parent."""
        parent.setdefault(id_, id_)
        if parent[id_] != id_:
            parent[id_] = root(parent[id_])
        return parent[id_]

    def join(ids: list[str]) -> None:
        """Union a connected set using its smallest root as representative."""
        roots = sorted({root(id_) for id_ in ids})
        for id_ in roots:
            parent[id_] = roots[0]

    strong = [event for event in events if not event["is_signal"]]
    for event in strong:
        join(event["student_ids"])
    groups, signals = defaultdict(list), []
    for event in events:
        ids = event["student_ids"]
        roots = {root(id_) for id_ in ids if id_ in parent}
        if ids and len(roots) == 1 and all(id_ in parent for id_ in ids):
            groups[("identity", next(iter(roots)))].append(event)
        else:
            signals.append(event)
    # A separate union-find for signals prevents weak links from merging proof clusters.
    parent = {}
    for event in signals:
        join(event["student_ids"])
    for event in signals:
        ids = event["student_ids"]
        subject = root(ids[0]) if ids else "institution:" + str(event["details"].get("institution_id", event["event_key"]))
        groups[("signal" if event["is_signal"] else "institution", subject)].append(event)
    result = []
    for (kind, subject), members in groups.items():
        ids = sorted({id_ for event in members for id_ in event["student_ids"]})
        result.append((_cluster_key(kind, ids or [subject]), members))
    return sorted(result)


def _recommend(events: list[dict], severity: str) -> str:
    """Return a human action, never an automatic sanction."""
    rules = {event["rule_key"] for event in events}
    if all(event["is_signal"] for event in events):
        return "Investigation signal: check context with the institution; shared references are not proof of duplicate identity."
    if "SIMULTANEOUS_ENROLMENT" in rules and severity in {"HIGH", "CRITICAL"}:
        return "Request institutional clarification from both institutions and place under review"
    if "EXCESS_INTAKE_VIOLATION" in rules:
        return "Review the rejected admission attempt against the approved seat matrix with the institution."
    return "Review the evidence and request institutional clarification; check corrections and transfer/migration context."


def _reference() -> str:
    """Allocate the required short reference, checking rare random collisions."""
    while True:
        reference = "CASE-" + secrets.token_hex(3).upper()
        if not db.session.execute(select(RiskCase.id).where(RiskCase.reference == reference)).first():
            return reference


def _save(events: list[dict], scan_id: str, actor: str, role: str, own: str | None,
          institution_subject: str | None = None) -> list[RiskCase]:
    """Reconcile cumulative evidence, history and policy scores in one audited unit."""
    weights, bands = _policies()
    incoming = _distinct(events, weights)
    if not incoming:
        return []
    ids = {id_ for event in incoming for id_ in event["student_ids"]}
    query = select(StudentRecord.id, StudentRecord.institution_id).where(StudentRecord.id.in_(ids))
    if own:
        query = query.where(StudentRecord.institution_id == own)
    records = dict(db.session.execute(query).all())
    if set(records) != ids:
        raise PermissionError("Risk events include unknown or inaccessible student records.")
    institutions = {event["details"].get("institution_id") for event in incoming} - {None}
    if own and (institutions - {own}):
        raise PermissionError("Risk events include another institution.")
    if institutions and set(db.session.execute(select(Institution.id).where(Institution.id.in_(institutions))).scalars()) != institutions:
        raise ValueError("Risk events include an unknown institution.")
    query = select(RiskCase)
    if own:
        query = query.where(RiskCase.institution_id == own)
    cases = {case.id: case for case in db.session.execute(query).scalars()}
    evidence = list(db.session.execute(select(RiskEvidence).where(RiskEvidence.case_id.in_(cases))).scalars())
    owned = [item for item in evidence if (item.details or {}).get("_risk", {}).get("engine") == ENGINE]
    if institution_subject:
        owned = [item for item in owned if item.details["_risk"].get("institution_subject") == institution_subject]
    else:
        owned = [item for item in owned if not item.details["_risk"].get("institution_subject")]
    historical = [_event({**item.to_dict(), "details": {key: value for key, value in (item.details or {}).items() if key != "_risk"}}) for item in owned]
    # Choose the same canonical rule regardless of scan order or repeated delivery.
    combined = {event["event_key"]: event for event in _distinct(historical + incoming, weights)}
    touched = {event["event_key"] for event in incoming}
    clusters = [(_cluster_key("excess", [institution_subject]), list(combined.values()))] if institution_subject else _clusters(list(combined.values()))
    results = []
    for cluster, members in clusters:
        keys = {event["event_key"] for event in members}
        if not keys.intersection(touched):
            continue
        candidates = {item.case_id for item in owned if item.event_key in keys}
        ordered = sorted((cases[id_] for id_ in candidates), key=lambda case: (case.opened_at, case.id))
        case = ordered[0] if ordered else RiskCase(id=str(uuid4()), reference=_reference(), status="OPEN", assigned_to=None)
        if not ordered:
            db.session.add(case)
            db.session.flush()
            db.session.add(CaseAction(case_id=case.id, actor_id="SYSTEM", action="UNDER_REVIEW",
                from_status="OPEN", to_status="OPEN", note="Case opened by system scan"))
        # Keep the oldest case's workflow. Preserve every action when clusters join.
        for other in ordered[1:]:
            for action in other.actions.all():
                action.case_id = case.id
            for item in other.evidence_items.all():
                db.session.delete(item)
            db.session.flush()
            db.session.delete(other)
        score, severity, breakdown = _score(members, weights, bands)
        student_ids = sorted({id_ for event in members for id_ in event["student_ids"]})
        # Historical members may extend beyond the current input, but only within authorized cases.
        affected_query = select(StudentRecord.id, StudentRecord.institution_id).where(StudentRecord.id.in_(student_ids))
        if own:
            affected_query = affected_query.where(StudentRecord.institution_id == own)
        affected_records = dict(db.session.execute(affected_query).all())
        if set(affected_records) != set(student_ids):
            raise PermissionError("Historical risk evidence includes unknown or inaccessible records.")
        affected = set(affected_records.values())
        affected.update(event["details"]["institution_id"] for event in members if event["details"].get("institution_id"))
        case.institution_id = next(iter(affected)) if len(affected) == 1 else None
        case.score, case.severity = score, severity
        case.summary = ("Investigation signal" if all(event["is_signal"] for event in members) else "Evidence requires human review") + f"; {len(student_ids)} affected records; {len(affected)} institutions."
        case.recommended_action = _recommend(members, severity)
        existing = {item.event_key: item for item in case.evidence_items.all()}
        for item in breakdown:
            values = {"rule_key": item["rule_key"], "points": item["points"], "description": item["description"],
                "related_student_ids": item["student_ids"], "details": {**item["details"], "is_signal": item["is_signal"],
                    "_risk": {"engine": ENGINE, "cluster_key": cluster, "institution_subject": institution_subject,
                              "affected_student_ids": student_ids, "affected_institution_ids": sorted(affected)}}}
            row = existing.get(item["event_key"])
            if row is None:
                db.session.add(RiskEvidence(case_id=case.id, event_key=item["event_key"], **values))
            else:
                for name, value in values.items():
                    setattr(row, name, value)
        results.append(case)
    db.session.flush()
    audit = audit_service.log(actor, "RISK_EVALUATED", "RISK_SCAN", scan_id,
        {"actor_role": role, "cases": [case.id for case in results], "event_count": len(incoming)}, institution_id=own or institution_subject)
    if not audit.get("id"):
        raise RuntimeError("Risk evaluation could not persist its audit record.")
    return results


def evaluate_all(scan_result: Any) -> list[RiskCase]:
    """Build/update cases without modifying students or credentials in any way.

    Evidence is cumulative: a partial scan cannot erase earlier evidence or human
    decisions. Re-evaluation refreshes policy points without appending history.
    """
    _ready()
    actor, role, own = _actor()
    try:
        return _save([_event(event) for event in scan_result.events], scan_result.scan_id, actor, role, own)
    except Exception:
        db.session.rollback()
        raise


def record_excess_intake_event(admission_event: AdmissionEvent) -> RiskCase | None:
    """Record a committed REJECTED capacity attempt, never punish its student.

    Accepted attempts and non-capacity rejections return None. The admission UUID
    is the evidence key; repeated delivery does not increase the score.
    """
    _ready()
    actor, role, own = _actor()
    if admission_event.result != "REJECTED" or admission_event.reason_code not in {"CAPACITY_EXCEEDED", "EXCESS_INTAKE_VIOLATION"}:
        return None
    stored = db.session.get(AdmissionEvent, admission_event.id)
    if stored is None or stored is not admission_event:
        raise ValueError("A persisted admission event is required.")
    if own and stored.institution_id != own:
        raise PermissionError("Only your own institution's admissions can be evaluated.")
    event = {"event_key": stored.id, "rule_key": "EXCESS_INTAKE_VIOLATION", "student_ids": [],
        "explanation": "Admission attempt rejected because approved seat capacity was exceeded; review with the institution.",
        "evidence": {"institution_id": stored.institution_id, "programme_id": stored.programme_id,
            "academic_year": stored.academic_year, "category": stored.category, "classification": stored.classification,
            "admission_event_id": stored.id, "affected_student_id": stored.student_id}, "is_signal": False}
    try:
        return _save([_event(event)], stored.id, actor, role, own, stored.institution_id)[0]
    except Exception:
        db.session.rollback()
        raise


def explain(case: RiskCase) -> str:
    """Explain stored evidence points and the score ceiling in plain English."""
    items = case.evidence_items.order_by(RiskEvidence.rule_key, RiskEvidence.event_key).all()
    terms = " + ".join(f"{item.points} {LABELS.get(item.rule_key, item.rule_key.lower().replace('_', ' '))}" for item in items) or "0 evidence points"
    signal_only = bool(items) and all((item.details or {}).get("is_signal") for item in items)
    cap = "signal-only safety cap: LOW, at most 29; investigation signal, not proof" if signal_only else "capped at 100"
    return f"Score {case.score} ({case.severity.title()}) = {terms} ({cap}). Human review required."
