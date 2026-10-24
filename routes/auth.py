"""
Authentication routes and session management for APAAR Nexus.
Includes API login/logout/me endpoints and HTML login view.
"""
from flask import Blueprint, request, jsonify, render_template, redirect, url_for, current_app, flash
from flask_login import login_user, logout_user, current_user, login_required

from models.user import User
from models.enums import Role
from services.authz import login_rate_limiter
from services.audit_service import log as audit_log

auth_bp = Blueprint("auth", __name__)


def get_client_ip() -> str:
    """Extract client IP address safely from request headers."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "127.0.0.1"


def get_role_home_url(role: str) -> str:
    """Determine initial landing dashboard route based on user role."""
    role_str = role.value if hasattr(role, "value") else str(role)
    if role_str in (Role.AICTE_ADMIN.value, Role.INSTITUTION_ADMIN.value):
        return "/dashboard"
    elif role_str == Role.STUDENT.value:
        return "/passport"
    elif role_str == Role.VERIFIER.value:
        return "/verify-console"
    elif role_str == Role.AUDITOR.value:
        return "/reports"
    return "/dashboard"


@auth_bp.route("/", methods=["GET"])
def root_redirect():
    """Render public landing page for visitors, or redirect authenticated users to their dashboard."""
    if current_user.is_authenticated:
        return redirect(get_role_home_url(current_user.role))
    return landing_page()


@auth_bp.route("/login", methods=["GET"])
def login_page():
    """Render the official portal login view."""
    if current_user.is_authenticated:
        return redirect(get_role_home_url(current_user.role))

    show_demo = current_app.config.get("SHOW_DEMO_ACCOUNTS", True)
    demo_password = current_app.config.get("DEMO_PASSWORD", "Demo@12345")
    return render_template(
        "login.html",
        show_demo_accounts=show_demo,
        demo_password=demo_password,
    )


@auth_bp.route("/landing", methods=["GET"])
def landing_page():
    """Render public overview and interactive guided tour."""
    show_demo = current_app.config.get("SHOW_DEMO_ACCOUNTS", True)
    demo_password = current_app.config.get("DEMO_PASSWORD", "Demo@12345")
    return render_template(
        "landing.html",
        show_demo_accounts=show_demo,
        demo_password=demo_password,
    )


@auth_bp.route("/api/auth/login", methods=["POST"])
def login_api():
    """Authenticate user with username and password, enforcing rate limits."""
    # Support both JSON payload and form-encoded data
    if request.is_json:
        data = request.get_json(silent=True) or {}
    else:
        data = request.form.to_dict()

    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    client_ip = get_client_ip()

    if not username or not password:
        return jsonify({
            "ok": False,
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "Both username and password are required.",
                "details": {}
            }
        }), 422

    # Rate limiting check
    is_locked, remaining_seconds = login_rate_limiter.is_locked(username, client_ip)
    if is_locked:
        return jsonify({
            "ok": False,
            "error": {
                "code": "RATE_LIMITED",
                "message": f"Too many failed login attempts. Please wait {remaining_seconds} seconds.",
                "details": {"retry_after": remaining_seconds}
            }
        }), 429

    user = User.query.filter_by(username=username).first()

    # Verify credentials
    if not user or not user.check_password(password):
        is_locked_now, lock_seconds = login_rate_limiter.record_failure(username, client_ip)
        audit_log(
            actor=username,
            action="AUTH_LOGIN_FAILURE",
            entity_type="USER",
            entity_id=username,
            details={"ip": client_ip, "reason": "INVALID_CREDENTIALS"},
            institution_id=None
        )
        if is_locked_now:
            return jsonify({
                "ok": False,
                "error": {
                    "code": "RATE_LIMITED",
                    "message": f"Too many failed login attempts. Please wait {lock_seconds} seconds.",
                    "details": {"retry_after": lock_seconds}
                }
            }), 429

        return jsonify({
            "ok": False,
            "error": {
                "code": "UNAUTHENTICATED",
                "message": "Invalid username or password.",
                "details": {}
            }
        }), 401

    # Check active status
    if not user.is_active:
        audit_log(
            actor=username,
            action="AUTH_LOGIN_FAILURE",
            entity_type="USER",
            entity_id=user.id,
            details={"ip": client_ip, "reason": "ACCOUNT_INACTIVE"},
            institution_id=user.institution_id
        )
        return jsonify({
            "ok": False,
            "error": {
                "code": "ACCOUNT_INACTIVE",
                "message": "This account is inactive. Please contact your system administrator.",
                "details": {}
            }
        }), 403

    # Successful authentication
    login_rate_limiter.record_success(username, client_ip)
    login_user(user, remember=False)

    audit_log(
        actor=user.username,
        action="AUTH_LOGIN_SUCCESS",
        entity_type="USER",
        entity_id=user.id,
        details={"ip": client_ip, "role": user.role},
        institution_id=user.institution_id
    )

    next_url = request.args.get("next") or get_role_home_url(user.role)
    return jsonify({
        "ok": True,
        "data": {
            "user": user.to_dict(),
            "redirect_url": next_url
        }
    }), 200


@auth_bp.route("/api/auth/logout", methods=["POST"])
@auth_bp.route("/logout", methods=["POST"])
@login_required
def logout():
    """Terminate current user session and record audit event."""
    user_id = current_user.id
    username = current_user.username
    inst_id = current_user.institution_id

    audit_log(
        actor=username,
        action="AUTH_LOGOUT",
        entity_type="USER",
        entity_id=user_id,
        details={},
        institution_id=inst_id
    )

    logout_user()

    if request.path.startswith("/api/") or (
        request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html
    ):
        return jsonify({
            "ok": True,
            "data": {"message": "Successfully logged out."}
        }), 200

    flash("You have been signed out.", "info")
    return redirect(url_for("auth.login_page"))


@auth_bp.route("/api/auth/me", methods=["GET"])
@login_required
def me():
    """Return profile attributes of current authenticated user (excluding password)."""
    return jsonify({
        "ok": True,
        "data": {
            "user": current_user.to_dict()
        }
    }), 200
