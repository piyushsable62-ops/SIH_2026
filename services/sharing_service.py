"""Student-controlled selective disclosure, not zero-knowledge proofs or identity issuance.

Tokens are bearer secrets returned once. Public resolution never changes academic
records; session counters, verification events and audit rows are the only writes.
"""
from datetime import timedelta
import base64
import hashlib
import hmac
import re
import secrets

from flask import current_app, has_request_context
from flask_login import current_user
from sqlalchemy import select, update

from extensions import db
from models import ConsentRecord, Credential, SharingSession, StudentRecord, SystemPolicy, User
from services import audit_service, credential_service as credentials

Error = credentials.CredentialError
CLAIMS = ("DEGREE_AUTHENTICITY", "INSTITUTION", "PROGRAMME", "AWARD_YEAR", "STUDENT_NAME", "APAAR_MASKED")
SENSITIVE = {"STUDENT_NAME", "APAAR_MASKED"}
INVALID_MESSAGE = "This sharing link is invalid or unavailable."


def utcnow():
    """Inject SHARING_CLOCK for deterministic server-side expiry checks."""
    clock = current_app.config.get("SHARING_CLOCK", credentials.utcnow)
    return credentials.aware(clock())


def allowed_expiry_hours() -> list[int]:
    """Allow policy to narrow the three supported expiry choices; fail closed."""
    policy = db.session.execute(select(SystemPolicy).where(SystemPolicy.key == "sharing_allowed_expiry_hours")).scalar_one_or_none()
    values = policy.value if policy else [24, 72, 168]
    if not isinstance(values, list) or not values or any(type(v) is not int or v not in (24, 72, 168) for v in values):
        raise Error("Sharing expiry policy is unavailable.", "CONFLICT", 409)
    return sorted(set(values))


def actor_user(actor=None) -> User:
    """Resolve a persisted active actor; request identities cannot be overridden."""
    if has_request_context():
        if not current_user.is_authenticated:
            raise Error("Authentication required.", "UNAUTHENTICATED", 401)
        if actor is not None and getattr(actor, "id", actor) != current_user.id:
            raise Error("Actor does not match the session.", "FORBIDDEN", 403)
        actor = current_user.id
    user = db.session.get(User, getattr(actor, "id", actor)) if actor is not None else None
    if user is None or not user.is_active:
        raise Error("An active authenticated actor is required.", "FORBIDDEN", 403)
    return user


def audit(user, action: str, row: SharingSession, details: dict) -> None:
    """Commit pending session changes and their audit row as one transaction."""
    result = audit_service.log(user, action, "SHARING_SESSION", row.id, details, institution_id=row.student.institution_id)
    if not result or not result.get("id"):
        raise RuntimeError("Sharing audit could not be persisted.")


def create_sharing_session(student: StudentRecord, credential_id: str, claims: list, expiry_hours: int,
                           actor=None, *, explicit_authorisation=None) -> tuple[SharingSession, str]:
    """Create consent and a hashed bearer session; return its raw token exactly once.

Each selected personal claim needs its own true authorisation in the mapping.
The profile name is a consented profile claim, not an Ed25519-signed name claim.
"""
    credentials.ready()
    user = actor_user(actor)
    try:
        if user.role != "STUDENT" or student is None or user.student_id != student.id:
            raise Error("Students may share only their own credentials.", "FORBIDDEN", 403)
        row = db.session.get(Credential, credential_id) if isinstance(credential_id, str) else None
        if row is None or row.student_id != student.id:
            raise Error("Credential not found in your scope.", "NOT_FOUND", 404)
        if not isinstance(claims, list) or not claims or len(claims) > len(CLAIMS) or any(not isinstance(c, str) or c not in CLAIMS for c in claims) or len(set(claims)) != len(claims):
            raise Error("Select one or more distinct supported claims.")
        flags = explicit_authorisation if explicit_authorisation is not None else {}
        if not isinstance(flags, dict) or set(flags) - SENSITIVE or any(type(v) is not bool for v in flags.values()):
            raise Error("explicit_authorisation must map each personal claim to a boolean.")
        if any(flags.get(c) is not True for c in set(claims) & SENSITIVE):
            raise Error("Explicit authorisation is required for each selected personal claim.")
        if type(expiry_hours) is not int or expiry_hours not in allowed_expiry_hours():
            raise Error("Choose a supported sharing expiry: 24, 72 or 168 hours.")
        outcome = credentials.verify_credential(row.reference, commit=False)
        if outcome.result != "VERIFIED":
            raise Error("Only a currently verified credential can be shared.", "CONFLICT", 409)
        ordered = [c for c in CLAIMS if c in claims]
        consent = ConsentRecord(student_id=student.id, claims=ordered)
        db.session.add(consent)
        db.session.flush()
        token = secrets.token_urlsafe(32)
        session = SharingSession(student_id=student.id, credential_id=row.id, consent_id=consent.id,
            token_hash=hashlib.sha256(token.encode()).hexdigest(), token_prefix=token[:6], claims=ordered,
            created_at=utcnow(), expires_at=utcnow() + timedelta(hours=expiry_hours))
        db.session.add(session)
        db.session.flush()
        audit(user, "SHARING_CREATED", session, {"claims": ordered, "explicit_authorisation": flags, "expiry_hours": expiry_hours})
        return session, token
    except Exception:
        db.session.rollback()
        raise


def session_status(row: SharingSession) -> str:
    """Revocation takes precedence over server-clock expiry."""
    return "REVOKED" if row.revoked_at else "EXPIRED" if credentials.aware(row.expires_at) <= utcnow() else "ACTIVE"


def metadata(row: SharingSession) -> dict:
    """Allowlist metadata: no token hash, raw token or student internal identifier."""
    credential = db.session.get(Credential, row.credential_id)
    return {"id": row.id, "credential_reference": credential.reference if credential else None,
        "claims": row.claims, "token_prefix": row.token_prefix, "created_at": credentials.iso(row.created_at),
        "expires_at": credentials.iso(row.expires_at), "status": session_status(row),
        "access_count": row.access_count, "last_accessed_at": credentials.iso(row.last_accessed_at)}


def list_sessions(student: StudentRecord) -> list[dict]:
    """List only a student's session metadata; authorization remains mandatory."""
    user = actor_user()
    if user.role not in {"AICTE_ADMIN", "AUDITOR"} and (user.role != "STUDENT" or student is None or user.student_id != student.id):
        raise Error("Sharing metadata is outside your scope.", "FORBIDDEN", 403)
    return [metadata(row) for row in db.session.execute(select(SharingSession).where(SharingSession.student_id == student.id)
        .order_by(SharingSession.created_at.desc())).scalars()]


def resolve_token(token: str) -> dict:
    """Resolve only consented claims and reverify signatures on every active access.

Unknown and malformed secrets use the same query and generic response. No raw
token is included in a returned structure, audit record or verification event.
"""
    credentials.ready()
    valid = isinstance(token, str) and re.fullmatch(r"[A-Za-z0-9_-]{43}", token) is not None
    digest = hashlib.sha256((token if valid else "invalid-sharing-token").encode()).hexdigest()
    row = db.session.execute(select(SharingSession).where(SharingSession.token_hash == digest)).scalar_one_or_none()
    if row is None or not valid:
        credentials.verification_event(None, "INVALID")
        db.session.commit()
        raise Error(INVALID_MESSAGE, "NOT_FOUND", 404)
    try:
        credential = db.session.get(Credential, row.credential_id)
        status = session_status(row)
        claims = {}
        if status == "ACTIVE":
            consent = row.consent
            if not consent or consent.student_id != row.student_id or set(row.claims) != set(consent.claims) or set(row.claims) - set(CLAIMS) or not credential or credential.student_id != row.student_id:
                status = "INVALID"
                credentials.verification_event(credential, status, row.id)
            else:
                outcome = credentials.verify_credential(credential.reference, commit=False, sharing_session_id=row.id)
                status = outcome.result
                if status == "VERIFIED":
                    payload = outcome.credential
                    available = {"DEGREE_AUTHENTICITY": "Verified", "INSTITUTION": payload["institution"],
                        "PROGRAMME": payload["programme"], "AWARD_YEAR": payload["award_year"],
                        "STUDENT_NAME": row.student.full_name, "APAAR_MASKED": payload["apaar_masked"]}
                    claims = {key: available[key] for key in row.claims}
        else:
            credentials.verification_event(credential, status, row.id)
        db.session.execute(update(SharingSession).where(SharingSession.id == row.id)
            .values(access_count=SharingSession.access_count + 1, last_accessed_at=utcnow()).execution_options(synchronize_session=False))
        user = current_user if has_request_context() and current_user.is_authenticated else None
        audit(user, "SHARING_ACCESSED", row, {"result": status})
        messages = {"VERIFIED": "The selected academic details are valid.", "EXPIRED": "This sharing link or credential has expired.",
            "REVOKED": "This sharing link was revoked by the student or its credential was revoked.", "INVALID": INVALID_MESSAGE}
        return {"result": status, "message": messages[status], "claims": claims, "expires_at": credentials.iso(row.expires_at)}
    except Exception:
        db.session.rollback()
        raise


def revoke_session(session_id: str, actor=None) -> SharingSession:
    """Student owner or national support administrator may revoke, with full audit."""
    credentials.ready()
    user = actor_user(actor)
    try:
        row = db.session.get(SharingSession, session_id)
        if row is None or (user.role != "AICTE_ADMIN" and (user.role != "STUDENT" or user.student_id != row.student_id)):
            raise Error("Sharing session not found in your scope.", "NOT_FOUND", 404)
        changed = db.session.execute(update(SharingSession).where(SharingSession.id == row.id, SharingSession.revoked_at.is_(None))
            .values(revoked_at=utcnow()).execution_options(synchronize_session=False))
        if changed.rowcount != 1:
            raise Error("Sharing session is already revoked.", "CONFLICT", 409)
        audit(user, "SHARING_REVOKED", row, {"support_action": user.role == "AICTE_ADMIN"})
        return row
    except Exception:
        db.session.rollback()
        raise


def seed_demo_sessions(credential: Credential, actor: User) -> str:
    """Create three synthetic consent examples without adding a student login.

    Only the development seed hook may call this trusted internal helper. The
    reproducible active bearer secret is returned to the seed summary once;
    database rows and audit events retain only its digest and short prefix.
    """
    if not current_app.config.get("SHOW_DEMO_ACCOUNTS") or has_request_context():
        raise RuntimeError("Demo sharing seed is available only to the development seed job.")
    if actor.role != "AICTE_ADMIN" or not actor.is_active or credentials.check_credential(credential).result != "VERIFIED":
        raise Error("A valid synthetic credential and administrator are required for demo sharing.", "CONFLICT", 409)
    secret = current_app.config["SECRET_KEY"]
    secret_bytes = secret.encode() if isinstance(secret, str) else secret
    claims = ["DEGREE_AUTHENTICITY", "AWARD_YEAR"]
    active_token = ""
    for state in ("ACTIVE", "EXPIRED", "REVOKED"):
        digest = hmac.new(secret_bytes, f"APAAR-NEXUS-DEMO-SHARE:{credential.reference}:{state}".encode(), hashlib.sha256).digest()
        token = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        existing = db.session.execute(select(SharingSession).where(SharingSession.token_hash == token_hash)).scalar_one_or_none()
        if existing:
            continue
        if state == "ACTIVE":
            active_token = token
        now = utcnow()
        consent = ConsentRecord(student_id=credential.student_id, claims=claims)
        db.session.add(consent)
        db.session.flush()
        row = SharingSession(student_id=credential.student_id, credential_id=credential.id, consent_id=consent.id,
            token_hash=token_hash, token_prefix=token[:6], claims=claims, created_at=now,
            expires_at=now + timedelta(hours=72) if state != "EXPIRED" else now - timedelta(hours=1))
        db.session.add(row)
        db.session.flush()
        audit(actor, "SHARING_CREATED", row, {"claims": claims, "expiry_hours": 72, "synthetic_demo": True})
        if state == "REVOKED":
            revoke_session(row.id, actor)
    return active_token
