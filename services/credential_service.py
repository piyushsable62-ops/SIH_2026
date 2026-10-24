"""Synthetic Ed25519 credentials; no government integration or automatic sanctions.

Public verification uses stored public keys only. Callers commit unrelated work
first; audit_service commits credential/event changes and audit in one unit.
"""
import base64
import binascii
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
import hmac
import json
import re
import secrets
import time
from collections import OrderedDict, deque
from threading import Lock
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from flask import current_app, has_request_context, request
from flask_login import current_user
from sqlalchemy import select, update

from extensions import db
from models import Credential, Institution, Programme, RiskCase, RiskEvidence, SigningKey
from models import StudentRecord, SystemPolicy, User, VerificationEvent
from services import audit_service, key_store
from services.apaar_adapter import mask_apaar
from services.authz import scope_query

ISSUERS = {"AICTE_Admin", "VESIT", "COEP", "VJTI"}
PAYLOAD_KEYS = {"credential_id", "student_ref", "apaar_masked", "institution", "programme", "award_year",
                "issue_date", "expires_at", "issuer_key_id", "version", "synthetic"}


class CredentialError(Exception):
    """Safe domain failure for API adapters, including warnings needing acknowledgement."""
    def __init__(self, message: str, code: str = "VALIDATION_ERROR", status: int = 422, details: dict | None = None):
        super().__init__(message)
        self.code, self.status, self.details = code, status, details or {}


@dataclass(frozen=True)
class VerificationOutcome:
    """Public result; INVALID outcomes never carry untrusted claims."""
    result: str
    reason: str
    credential: dict
    key_id: str | None

    def to_dict(self) -> dict:
        """Serialize only the safe outcome fields."""
        return asdict(self)


def utcnow() -> datetime:
    """Use an injectable UTC clock for deterministic expiry tests."""
    clock = current_app.config.get("CREDENTIAL_CLOCK")
    return aware(clock() if clock else datetime.now(timezone.utc))


def aware(value: datetime) -> datetime:
    """Treat SQLite's naive UTC datetime columns consistently."""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def iso(value: datetime | None) -> str | None:
    """Serialize nullable expiry instants in UTC."""
    return aware(value).isoformat() if value is not None else None


def canonicalize(payload: dict) -> bytes:
    """The single canonical UTF-8 representation used for signing and checking."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def _reference() -> str:
    """Allocate a checked human-readable reference without exposing student identifiers."""
    for _ in range(10):
        value = "CRED-" + secrets.token_hex(3).upper()
        if not db.session.execute(select(Credential.id).where(Credential.reference == value)).first():
            return value
    raise CredentialError("Unable to allocate a credential reference.", "CONFLICT", 409)


def build_payload(student: StudentRecord, institution: Institution, programme: Programme, award_year: str,
                  issue_date: date, expires_at: datetime | None, *, credential_id: str | None = None,
                  issuer_key_id: str | None = None) -> dict:
    """Allowlist academic claims only; never copy arbitrary model dictionaries."""
    return {"credential_id": credential_id or _reference(), "student_ref": student.institution_student_id,
        "apaar_masked": mask_apaar(student.apaar_ref), "institution": {"code": institution.code, "name": institution.name},
        "programme": programme.name, "award_year": str(award_year), "issue_date": issue_date.isoformat(),
        "expires_at": iso(expires_at), "issuer_key_id": issuer_key_id or key_store.get_active_key_id(),
        "version": 1, "synthetic": True}


def ready() -> None:
    """Prevent an audit-owned commit from accidentally committing unrelated edits."""
    if db.session.new or db.session.dirty or db.session.deleted:
        raise CredentialError("Commit pending changes before calling this service.", "CONFLICT", 409)


def issuer(actor: Any = None) -> User:
    """Resolve a persisted official issuer; request callers cannot spoof another actor."""
    if has_request_context():
        if not current_user.is_authenticated:
            raise CredentialError("Authentication required.", "UNAUTHENTICATED", 401)
        user = db.session.get(User, current_user.id)
        if actor is not None and actor not in (user.username, user.id, user) and getattr(actor, "id", None) != user.id:
            raise CredentialError("Actor does not match the authenticated user.", "FORBIDDEN", 403)
    else:
        key = getattr(actor, "id", actor)
        user = db.session.execute(select(User).where((User.id == key) | (User.username == key))).scalar_one_or_none() if isinstance(key, str) else None
    if user is None or not user.is_active or user.username not in ISSUERS or user.role not in {"AICTE_ADMIN", "INSTITUTION_ADMIN"}:
        raise CredentialError("An authorized seeded institution or AICTE administrator is required.", "FORBIDDEN", 403)
    return user


def authorize_institution(user: User, institution_id: str) -> None:
    """Enforce the issuer's own institution for non-national administrators."""
    if user.role == "INSTITUTION_ADMIN" and user.institution_id != institution_id:
        raise CredentialError("Only your institution's credentials may be changed.", "FORBIDDEN", 403)


def _audit(user: User | None, action: str, entity: str, details: dict, institution_id: str | None = None) -> None:
    """Commit audit and pending credential/event rows together, never log secrets."""
    result = audit_service.log(user.username if user else "PUBLIC", action, "CREDENTIAL", entity,
        {"actor_role": user.role if user else "PUBLIC", **details}, institution_id=institution_id)
    if not result.get("id"):
        raise RuntimeError("Credential operation could not persist its audit record.")


def warnings_for(student: StudentRecord) -> list[dict]:
    """Open cases prompt human acknowledgement; risk never decides eligibility."""
    query = select(RiskCase).join(RiskEvidence).where(RiskEvidence.related_student_ids.contains(student.id),
        RiskCase.status.notin_(["RESOLVED", "FALSE_POSITIVE"])).distinct()
    return [{"reference": row.reference, "severity": row.severity, "status": row.status,
             "message": "Open investigation: review the evidence before issuing."} for row in db.session.execute(query).scalars()]


def issue_credential(student_id: str, award_year: Any, programme_id: str | None = None,
                     valid_years: int | None = None, actor: Any = None, *, acknowledge_warnings: bool = False) -> Credential:
    """Issue after human acknowledgement; never auto-flag students or alter their records."""
    ready()
    user = issuer(actor)
    try:
        query = select(StudentRecord).where(StudentRecord.id == student_id)
        if has_request_context():
            query = scope_query(query, StudentRecord)
        student = db.session.execute(query).scalar_one_or_none()
        if student is None:
            raise CredentialError("Student not found in your scope.", "NOT_FOUND", 404)
        authorize_institution(user, student.institution_id)
        if student.enrolment_status not in {"ACTIVE", "GRADUATED"} or student.validation_status != "VALID" or not student.is_synthetic:
            raise CredentialError("A synthetic ACTIVE or GRADUATED student with VALID data is required.")
        if isinstance(award_year, bool) or not re.fullmatch(r"\d{4}", str(award_year)) or not 1900 <= int(award_year) <= 9999:
            raise CredentialError("award_year must be a four-digit year from 1900.")
        if programme_id is not None and programme_id != student.programme_id:
            raise CredentialError("Programme must match the student's academic record.")
        if valid_years is None:
            policy = db.session.execute(select(SystemPolicy).where(SystemPolicy.key == "credential_default_validity_years")).scalar_one_or_none()
            valid_years = policy.value if policy else 4
        if isinstance(valid_years, bool) or not isinstance(valid_years, int) or not 1 <= valid_years <= 100:
            raise CredentialError("valid_years must be an integer from 1 to 100.")
        if type(acknowledge_warnings) is not bool:
            raise CredentialError("acknowledge_warnings must be a boolean.")
        warnings = warnings_for(student)
        if warnings and not acknowledge_warnings:
            raise CredentialError("Review and explicitly acknowledge the open investigation warnings.", "CONFLICT", 409, {"warnings": warnings})
        existing = db.session.execute(select(Credential.id).where(Credential.student_id == student.id,
            Credential.award_year == str(award_year), Credential.programme_name == student.programme.name,
            Credential.status_stored == "ISSUED")).first()
        if existing:
            raise CredentialError("An issued credential already exists for this student, programme and award year.", "CONFLICT", 409)
        key_id = key_store.get_active_key_id()
        metadata = db.session.execute(select(SigningKey).where(SigningKey.key_id == key_id, SigningKey.is_active == True)).scalar_one_or_none()
        if metadata is None or metadata.algorithm != "Ed25519":
            raise CredentialError("Active Ed25519 public key metadata is unavailable.", "CONFLICT", 409)
        private = key_store.load_private_key(key_id)
        public = serialization.load_pem_public_key(metadata.public_key_pem.encode())
        if not isinstance(private, Ed25519PrivateKey) or not isinstance(public, Ed25519PublicKey) or private.public_key().public_bytes_raw() != public.public_bytes_raw():
            raise CredentialError("Signing key does not match its registered public key.", "CONFLICT", 409)
        now = utcnow()
        try:
            expires = now.replace(year=now.year + valid_years)
        except ValueError:
            expires = now.replace(year=now.year + valid_years, day=28)
        payload = build_payload(student, student.institution, student.programme, str(award_year), now.date(), expires)
        payload["issuer_key_id"] = key_id
        encoded = canonicalize(payload)
        row = Credential(reference=payload["credential_id"], student_id=student.id, institution_id=student.institution_id,
            programme_name=student.programme.name, award_year=str(award_year), issue_date=now.date(), expires_at=expires,
            payload_json=encoded.decode("utf-8"), signature=base64.b64encode(private.sign(encoded)).decode("ascii"),
            key_id=key_id, status_stored="ISSUED")
        db.session.add(row)
        db.session.flush()
        _audit(user, "CREDENTIAL_ISSUED", row.id, {"reference": row.reference, "warnings_acknowledged": bool(warnings)}, row.institution_id)
        row.warnings = warnings
        return row
    except Exception:
        db.session.rollback()
        raise


def check_credential(row: Credential | None) -> VerificationOutcome:
    """Check stored bytes, public signature, database binding, revocation, then expiry."""
    invalid = lambda reason: VerificationOutcome("INVALID", reason, {}, None)
    if row is None:
        return invalid("not found")
    try:
        key = db.session.execute(select(SigningKey).where(SigningKey.key_id == row.key_id)).scalar_one_or_none()
        if key is None or key.algorithm != "Ed25519":
            return invalid("Signing key unavailable")
        public = serialization.load_pem_public_key(key.public_key_pem.encode())
        if not isinstance(public, Ed25519PublicKey) or not isinstance(row.payload_json, str) or len(row.payload_json) > 16000:
            return invalid("Signature mismatch")
        stored = row.payload_json.encode("utf-8")
        public.verify(base64.b64decode(row.signature, validate=True), stored)
        payload = json.loads(stored)
        if not isinstance(payload, dict) or set(payload) != PAYLOAD_KEYS or canonicalize(payload) != stored:
            return invalid("Record altered")
        student = db.session.get(StudentRecord, row.student_id)
        institution = db.session.get(Institution, row.institution_id)
        if student is None or institution is None:
            return invalid("Record altered")
        expected = {"credential_id": row.reference, "student_ref": student.institution_student_id,
            "apaar_masked": mask_apaar(student.apaar_ref), "institution": {"code": institution.code, "name": institution.name},
            "programme": row.programme_name, "award_year": row.award_year, "issue_date": row.issue_date.isoformat(),
            "expires_at": iso(row.expires_at), "issuer_key_id": row.key_id, "version": 1, "synthetic": True}
        if canonicalize(expected) != stored:
            return invalid("Record altered")
        if row.status_stored == "REVOKED":
            return VerificationOutcome("REVOKED", "Revoked by the issuer", {"credential_id": row.reference,
                "revoked_at": iso(row.revoked_at), "reason_category": "REVOKED_BY_ISSUER"}, row.key_id)
        if row.status_stored != "ISSUED":
            return invalid("Record altered")
        if row.expires_at is not None and aware(row.expires_at) <= utcnow():
            return VerificationOutcome("EXPIRED", "Credential has expired", {"credential_id": row.reference, "expires_at": iso(row.expires_at)}, row.key_id)
        safe = {key: value for key, value in payload.items() if key != "student_ref"}
        return VerificationOutcome("VERIFIED", "Ed25519 signature and stored academic claims are valid", safe, row.key_id)
    except (InvalidSignature, ValueError, TypeError, AttributeError, binascii.Error, UnicodeError):
        return invalid("Signature mismatch or record altered")


def verification_event(row: Credential | None, result: str, sharing_session_id: str | None = None) -> VerificationEvent:
    """Record pseudonymous verifier metadata; never save raw IPs or tokens."""
    user_id = current_user.id if has_request_context() and current_user.is_authenticated else None
    ip = request.remote_addr if has_request_context() else None
    digest = hmac.new(str(current_app.config["SECRET_KEY"]).encode(), (ip or "SYSTEM").encode(), "sha256").hexdigest()
    event = VerificationEvent(credential_id=row.id if row else None, sharing_session_id=sharing_session_id,
        result=result, verifier_user_id=user_id, ip_hash=digest)
    db.session.add(event)
    return event


class PublicLimiter:
    """Bounded per-process 30/minute limiter; keys are hashed remote addresses."""
    def __init__(self):
        self.entries = OrderedDict()
        self.lock = Lock()

    def allow(self, key: str, now: float) -> bool:
        """Atomically discard expired entries and admit at most thirty requests."""
        with self.lock:
            bucket = self.entries.setdefault(key, deque())
            self.entries.move_to_end(key)
            while bucket and bucket[0] <= now - 60:
                bucket.popleft()
            if len(self.entries) > 10000:
                self.entries.popitem(last=False)
            if len(bucket) >= 30:
                return False
            bucket.append(now)
            return True


def public_limit(area: str) -> None:
    """Apply a shared-IP bucket independent of reference/token and response format."""
    limiter = current_app.extensions.setdefault("nexus_public_limiter", PublicLimiter())
    clock = current_app.config.get("PUBLIC_RATE_CLOCK", time.monotonic)
    key = area + hmac.new(str(current_app.config["SECRET_KEY"]).encode(), (request.remote_addr or "unknown").encode(), "sha256").hexdigest()
    if not limiter.allow(key, clock()):
        raise CredentialError("Too many verification requests. Try again in a minute.", "RATE_LIMITED", 429)


def verify_credential(reference: str, *, commit: bool = True, sharing_session_id: str | None = None) -> VerificationOutcome:
    """Verify publicly and record every call, including unknown or malformed references.

    commit=False is for sharing's encompassing audited transaction only.
    """
    if commit:
        ready()
    try:
        row = db.session.execute(select(Credential).where(Credential.reference == reference)).scalar_one_or_none() if isinstance(reference, str) and len(reference) <= 30 else None
        result = check_credential(row)
        verification_event(row, result.result, sharing_session_id)
        if commit:
            user = current_user if has_request_context() and current_user.is_authenticated else None
            _audit(user, "CREDENTIAL_VERIFICATION", row.id if row else "UNKNOWN", {"result": result.result})
        return result
    except Exception:
        db.session.rollback()
        raise


def revoke_credential(credential_id: str, reason: str, actor: Any = None) -> Credential:
    """Revoke only after an authorized human reason; duplicate revocation is CONFLICT."""
    ready()
    user = issuer(actor)
    try:
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
            raise CredentialError("A revocation reason of 1–2000 characters is required.")
        query = select(Credential).where(Credential.id == credential_id)
        if has_request_context():
            query = scope_query(query, Credential)
        row = db.session.execute(query).scalar_one_or_none()
        if row is None:
            raise CredentialError("Credential not found in your scope.", "NOT_FOUND", 404)
        authorize_institution(user, row.institution_id)
        changed = db.session.execute(update(Credential).where(Credential.id == row.id, Credential.status_stored == "ISSUED")
            .values(status_stored="REVOKED", revoked_at=utcnow(), revoked_by=user.username, revoke_reason=reason.strip())
            .execution_options(synchronize_session=False))
        if changed.rowcount != 1:
            raise CredentialError("Credential is already revoked or not issuable.", "CONFLICT", 409)
        _audit(user, "CREDENTIAL_REVOKED", row.id, {"reason_category": "REVOKED_BY_ISSUER"}, row.institution_id)
        return row
    except Exception:
        db.session.rollback()
        raise
