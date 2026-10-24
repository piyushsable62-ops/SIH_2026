"""
Audit service for logging all mutating actions in APAAR Nexus (Task A11).
Ensures accountability, transparency, non-repudiation, and multi-tenant isolation.
Persists records to the audit_logs table while scrubbing sensitive data.

APPEND-ONLY CONTRACT:
Audit rows are strictly append-only by design. No update, delete, or truncation
functions exist in this service or across the application. Once written, audit
events form an immutable, permanent historical log.
"""
from typing import Optional, Any, Dict, List, Tuple
from datetime import datetime
import re
import logging
from flask import current_app, has_app_context
from flask_login import current_user

from extensions import db
from models.audit import AuditLog
from services.apaar_adapter import mask_apaar

logger = logging.getLogger("audit")

# Keys that must never appear in persisted audit details
SENSITIVE_KEYS = {
    "password", "password_hash", "token", "secret", "secret_key",
    "private_key", "private_key_pem", "raw_token", "full_token"
}


def _scrub_sensitive_data(data: Any) -> Any:
    """
    Recursively scrub sensitive keys and tokens from audit details.
    Ensures passwords, private keys, session tokens, and full 12-digit APAAR
    identifiers never leak into persisted audit logs or API responses.
    """
    if isinstance(data, dict):
        scrubbed = {}
        for k, v in data.items():
            k_lower = str(k).lower()
            if any(s in k_lower for s in SENSITIVE_KEYS):
                scrubbed[k] = "[REDACTED]"
            elif "apaar" in k_lower and isinstance(v, (str, int)):
                scrubbed[k] = mask_apaar(str(v))
            elif isinstance(v, str) and re.fullmatch(r"^\d{12}$", v.strip()):
                scrubbed[k] = mask_apaar(v.strip())
            else:
                scrubbed[k] = _scrub_sensitive_data(v)
        return scrubbed
    elif isinstance(data, list):
        return [_scrub_sensitive_data(item) for item in data]
    elif isinstance(data, str) and re.fullmatch(r"^\d{12}$", data.strip()):
        return mask_apaar(data.strip())
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
    Log and persist an immutable audit event.
    Appends a new row to audit_logs; does not modify or delete existing rows.

    Args:
        actor: Username or system actor performing the action.
        action: Specific action performed (e.g. ADMISSION_ACCEPTED, CREATE_STUDENT).
        entity_type: Target entity type (e.g. STUDENT, POLICY, INSTITUTION, REPORT).
        entity_id: Primary identifier of the affected entity.
        details: Optional dictionary containing event details and context.
        institution_id: Optional UUID of the scoped institution.

    Returns:
        Dict containing recorded audit event payload.
    """
    clean_details = _scrub_sensitive_data(details or {})
    actor_name = getattr(actor, "username", str(actor)) if actor is not None else "SYSTEM"

    # Detect actor role from Flask-Login context if available
    actor_role = None
    if has_app_context() and current_user and getattr(current_user, "is_authenticated", False):
        role_val = getattr(current_user, "role", None)
        actor_role = role_val.value if hasattr(role_val, "value") else str(role_val) if role_val else None

    # If actor role was provided in details, allow it to override
    if details and "actor_role" in details:
        actor_role = str(details.get("actor_role"))

    # Log to application logger
    if has_app_context():
        current_app.logger.info(
            "AUDIT: actor=%s action=%s entity=%s:%s inst=%s details=%s",
            actor_name,
            action,
            entity_type,
            entity_id,
            institution_id,
            clean_details,
        )

        try:
            entry = AuditLog(
                actor_id=actor_name,
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
        "actor": actor_name,
        "actor_role": actor_role,
        "action": action,
        "entity_type": entity_type,
        "entity_id": str(entity_id),
        "institution_id": str(institution_id) if institution_id else None,
        "details": clean_details,
        "created_at": created_at,
    }


def query_audit_logs(
    actor: Optional[str] = None,
    action: Optional[str] = None,
    entity_type: Optional[str] = None,
    institution_id: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    page: int = 1,
    per_page: int = 25
) -> Tuple[List[Dict[str, Any]], int]:
    """
    Query append-only audit logs with filtering and pagination.
    Returns (list_of_sanitized_audit_dicts, total_count).
    """
    query = AuditLog.query

    if institution_id:
        query = query.filter(AuditLog.institution_id == institution_id)

    if actor:
        query = query.filter(AuditLog.actor_id.ilike(f"%{actor.strip()}%"))

    if action:
        query = query.filter(AuditLog.action == action.strip().upper())

    if entity_type:
        query = query.filter(AuditLog.entity_type == entity_type.strip().upper())

    if date_from:
        try:
            dt_from = datetime.fromisoformat(date_from.strip())
            query = query.filter(AuditLog.created_at >= dt_from)
        except ValueError:
            pass

    if date_to:
        try:
            # End of day
            dt_to = datetime.fromisoformat(date_to.strip())
            if len(date_to.strip()) <= 10:
                dt_to = dt_to.replace(hour=23, minute=59, second=59)
            query = query.filter(AuditLog.created_at <= dt_to)
        except ValueError:
            pass

    total = query.count()
    logs = (
        query.order_by(AuditLog.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    items = []
    for entry in logs:
        d = entry.to_dict()
        # Ensure details are sanitized
        d["details"] = _scrub_sensitive_data(d.get("details") or {})
        items.append(d)

    return items, total
