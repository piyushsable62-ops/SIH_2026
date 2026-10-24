"""
Audit service for logging all mutating actions in APAAR Nexus.
Ensures accountability, transparency, and non-repudiation.
Persists records to the audit_logs table while scrubbing sensitive data.
"""
from typing import Optional, Any, Dict
import logging
from flask import current_app, has_app_context
from flask_login import current_user

from extensions import db
from models.audit import AuditLog

logger = logging.getLogger("audit")

# Keys that must never appear in persisted audit details
SENSITIVE_KEYS = {
    "password", "password_hash", "token", "secret", "secret_key",
    "private_key", "private_key_pem", "raw_token", "full_token"
}


def _scrub_sensitive_data(data: Any) -> Any:
    """Recursively scrub sensitive keys and tokens from audit details."""
    if isinstance(data, dict):
        scrubbed = {}
        for k, v in data.items():
            if any(s in k.lower() for s in SENSITIVE_KEYS):
                scrubbed[k] = "[REDACTED]"
            elif k.lower() in ("apaar", "apaar_ref") and isinstance(v, str) and len(v) >= 4:
                scrubbed[k] = f"XXXXXXXX{v[-4:]}"
            else:
                scrubbed[k] = _scrub_sensitive_data(v)
        return scrubbed
    elif isinstance(data, list):
        return [_scrub_sensitive_data(item) for item in data]
    return data


def log(
    actor: str,
    action: str,
    entity_type: str,
    entity_id: str,
    details: Optional[Dict[str, Any]] = None,
    institution_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Log and persist an audit event.

    Args:
        actor: Username or system actor performing the action.
        action: Specific action performed (e.g. ADMISSION_ACCEPTED, CREDENTIAL_ISSUED).
        entity_type: Target entity type (e.g. STUDENT, CREDENTIAL, RISK_CASE).
        entity_id: Primary identifier of the affected entity.
        details: Optional dictionary containing event details and context.
        institution_id: Optional UUID of the scoped institution.

    Returns:
        Dict containing recorded audit event payload.
    """
    clean_details = _scrub_sensitive_data(details or {})

    # Detect actor role from Flask-Login context if available
    actor_role = None
    if has_app_context() and current_user and getattr(current_user, "is_authenticated", False):
        actor_role = getattr(current_user, "role", None)

    # If actor role was provided in details, allow it to override
    if details and "actor_role" in details:
        actor_role = str(details.get("actor_role"))

    # Log to application logger
    if has_app_context():
        current_app.logger.info(
            "AUDIT: actor=%s action=%s entity=%s:%s inst=%s details=%s",
            actor,
            action,
            entity_type,
            entity_id,
            institution_id,
            clean_details,
        )

        try:
            entry = AuditLog(
                actor_id=str(actor),
                actor_role=actor_role,
                action=str(action),
                entity_type=str(entity_type),
                entity_id=str(entity_id),
                institution_id=str(institution_id) if institution_id else None,
                details=clean_details,
            )
            db.session.add(entry)
            db.session.commit()
            record_id = entry.id
            created_at = entry.created_at.isoformat()
        except Exception as exc:
            db.session.rollback()
            current_app.logger.error("Failed to persist audit log: %s", exc)
            record_id = None
            created_at = None
    else:
        logger.info("AUDIT: actor=%s action=%s entity=%s:%s", actor, action, entity_type, entity_id)
        record_id = None
        created_at = None

    return {
        "id": record_id,
        "actor": actor,
        "actor_role": actor_role,
        "action": action,
        "entity_type": entity_type,
        "entity_id": str(entity_id),
        "institution_id": str(institution_id) if institution_id else None,
        "details": clean_details,
        "created_at": created_at,
    }
