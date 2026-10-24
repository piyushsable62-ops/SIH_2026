"""
Authorization and RBAC service for APAAR Nexus.
Provides role enforcement decorators, query scoping, and login rate limiting.
"""
from functools import wraps
from typing import Optional, Any
import time
import threading
from collections import defaultdict

from flask import request, redirect, url_for, jsonify, abort
from flask_login import current_user
import sqlalchemy

from models.enums import Role


class LoginRateLimiter:
    """
    In-memory rate limiter tracking failed login attempts by (username, client_ip).
    Blocks access for lockout_seconds (60s) after max_failures (5) consecutive failures.
    """
    def __init__(self, max_failures: int = 5, lockout_seconds: int = 60):
        self.max_failures = max_failures
        self.lockout_seconds = lockout_seconds
        self._lock = threading.Lock()
        self._failures = defaultdict(list)
        self._lockouts = {}

    def _get_key(self, username: str, ip: str) -> str:
        clean_user = (username or "").strip().lower()
        clean_ip = (ip or "").strip()
        return f"{clean_user}:{clean_ip}"

    def is_locked(self, username: str, ip: str) -> tuple[bool, int]:
        """Check if an account/IP pair is currently locked out."""
        key = self._get_key(username, ip)
        now = time.time()
        with self._lock:
            expiry = self._lockouts.get(key)
            if expiry and now < expiry:
                return True, int(expiry - now) + 1
            elif expiry and now >= expiry:
                del self._lockouts[key]
                self._failures.pop(key, None)
            return False, 0

    def record_failure(self, username: str, ip: str) -> tuple[bool, int]:
        """Record a login failure and trigger lockout if limit reached."""
        key = self._get_key(username, ip)
        now = time.time()
        with self._lock:
            recent = [t for t in self._failures[key] if now - t < self.lockout_seconds]
            recent.append(now)
            self._failures[key] = recent
            if len(recent) >= self.max_failures:
                expiry = now + self.lockout_seconds
                self._lockouts[key] = expiry
                return True, self.lockout_seconds
            return False, 0

    def record_success(self, username: str, ip: str) -> None:
        """Clear failures on successful login."""
        key = self._get_key(username, ip)
        with self._lock:
            self._failures.pop(key, None)
            self._lockouts.pop(key, None)

    def reset_all(self) -> None:
        """Reset all rate limiter state (used in testing)."""
        with self._lock:
            self._failures.clear()
            self._lockouts.clear()


login_rate_limiter = LoginRateLimiter(max_failures=5, lockout_seconds=60)


def roles_required(*roles: Any):
    """
    Enforce that the authenticated user possesses one of the allowed roles.
    Returns 401 JSON/redirect if unauthenticated.
    Returns 403 JSON envelope if role unauthorized.
    """
    allowed_roles = {r.value if hasattr(r, "value") else str(r) for r in roles}

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                if request.path.startswith("/api/") or (
                    request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html
                ):
                    return jsonify({
                        "ok": False,
                        "error": {
                            "code": "UNAUTHENTICATED",
                            "message": "Authentication is required to access this resource.",
                            "details": {}
                        }
                    }), 401
                return redirect(url_for("auth.login_page", next=request.url))

            user_role = current_user.role.value if hasattr(current_user.role, "value") else str(current_user.role)
            if allowed_roles and user_role not in allowed_roles:
                if request.path.startswith("/api/") or (
                    request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html
                ):
                    return jsonify({
                        "ok": False,
                        "error": {
                            "code": "FORBIDDEN",
                            "message": f"Role '{user_role}' is not authorized to access this resource.",
                            "details": {"required_roles": sorted(list(allowed_roles))}
                        }
                    }), 403
                abort(403, description=f"Role '{user_role}' is not authorized to access this resource.")

            return fn(*args, **kwargs)
        return wrapper
    return decorator


def current_institution_id() -> Optional[str]:
    """Return the institution ID associated with the current user, or None."""
    if current_user and current_user.is_authenticated:
        return getattr(current_user, "institution_id", None)
    return None


def scope_query(query, model):
    """
    Scope a SQLAlchemy query based on the active user's role and institution.

    - AICTE_ADMIN: Unrestricted access across all institutions.
    - INSTITUTION_ADMIN: Strictly filtered to user's assigned institution.
    - STUDENT: Strictly filtered to user's own student record.
    - AUDITOR: Read access to reports, risk cases, and audit logs.
    - Others: Denied (empty result set).
    """
    if not current_user or not current_user.is_authenticated:
        return query.filter(sqlalchemy.false())

    user_role = current_user.role.value if hasattr(current_user.role, "value") else str(current_user.role)

    # AICTE administrators have national oversight
    if user_role == Role.AICTE_ADMIN.value:
        return query

    # Institution administrators are scoped to their institution only
    if user_role == Role.INSTITUTION_ADMIN.value:
        inst_id = current_user.institution_id
        if not inst_id:
            return query.filter(sqlalchemy.false())

        if hasattr(model, "__tablename__") and model.__tablename__ == "institutions":
            return query.filter(model.id == inst_id)
        elif hasattr(model, "institution_id"):
            return query.filter(model.institution_id == inst_id)
        return query

    # Students can only view their own student record and credentials
    if user_role == Role.STUDENT.value:
        student_id = current_user.student_id
        if not student_id:
            return query.filter(sqlalchemy.false())

        if hasattr(model, "__tablename__") and model.__tablename__ == "student_records":
            return query.filter(model.id == student_id)
        elif hasattr(model, "student_id"):
            return query.filter(model.student_id == student_id)
        return query.filter(sqlalchemy.false())

    # Auditors have read access to oversight tables
    if user_role == Role.AUDITOR.value:
        table_name = getattr(model, "__tablename__", "")
        if table_name in ("audit_logs", "risk_cases", "risk_evidence", "case_actions", "institutions", "programmes", "intake_capacities"):
            return query
        elif hasattr(model, "institution_id") and current_user.institution_id:
            return query.filter(model.institution_id == current_user.institution_id)
        return query

    # Default deny for unhandled scopes
    return query.filter(sqlalchemy.false())


def assert_same_institution(obj: Any) -> bool:
    """
    Verify that an institution administrator is not accessing or mutating
    another institution's resource.
    Raises 403 Forbidden with standard envelope if violated.
    """
    if not current_user or not current_user.is_authenticated:
        abort(401, description="Authentication required.")

    user_role = current_user.role.value if hasattr(current_user.role, "value") else str(current_user.role)
    if user_role == Role.AICTE_ADMIN.value:
        return True

    if user_role == Role.INSTITUTION_ADMIN.value:
        user_inst = current_user.institution_id
        if not user_inst:
            abort(403, description="User is not bound to any institution.")

        target_inst = getattr(obj, "institution_id", None)
        if target_inst is None and getattr(obj, "__tablename__", "") == "institutions":
            target_inst = getattr(obj, "id", None)

        if target_inst != user_inst:
            abort(403, description="Cross-institution access denied.")
        return True

    return True

