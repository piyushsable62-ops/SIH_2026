"""
APAAR Nexus Application Factory and Entry Point.
Smart India Hackathon Problem Statement PS 26263.
Prototype notice: Synthetic data only. Not connected to AICTE, APAAR, UIDAI, or DigiLocker.
"""
import os
import logging
from flask import Flask, jsonify, request
from flask_wtf.csrf import CSRFError
from werkzeug.exceptions import HTTPException

from config import config_by_name
from extensions import db, login_manager, csrf
import routes


def create_app(config_name: str = None) -> Flask:
    """
    Application factory for APAAR Nexus.

    Args:
        config_name: Environment configuration key ('development', 'testing', 'production').

    Returns:
        Configured Flask application instance.
    """
    if config_name is None:
        config_name = os.getenv("FLASK_ENV", "development")

    config_class = config_by_name.get(config_name, config_by_name["default"])

    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config_class)

    # Ensure instance directory exists
    try:
        os.makedirs(app.instance_path, exist_ok=True)
        keys_dir = os.path.join(app.instance_path, "keys")
        os.makedirs(keys_dir, exist_ok=True)
    except OSError:
        pass

    # Initialize extensions
    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)

    # Configure login manager
    login_manager.login_view = "auth.login_page"
    login_manager.login_message_category = "info"
    login_manager.session_protection = "strong"

    # Register blueprints
    routes.register_all(app)

    # Context processor for Jinja2 templates
    @app.context_processor
    def inject_global_template_vars():
        return {
            "app_name": app.config.get("APP_NAME", "APAAR Nexus"),
            "app_version": app.config.get("APP_VERSION", "1.0.0-prototype"),
            "prototype_banner": app.config.get(
                "SYNTHETIC_NOTICE",
                "Prototype — synthetic data only. Not connected to AICTE, APAAR, UIDAI or DigiLocker."
            ),
            "show_demo_accounts": app.config.get("SHOW_DEMO_ACCOUNTS", True),
            "ui_paths": {rule.rule for rule in app.url_map.iter_rules() if "GET" in rule.methods},
            "ui_endpoints": set(app.view_functions),
        }

    # Error code to envelope mapping
    def make_error_envelope(code: str, message: str, status_code: int, details: dict = None):
        return jsonify({
            "ok": False,
            "error": {
                "code": code,
                "message": message,
                "details": details or {}
            }
        }), status_code

    @login_manager.unauthorized_handler
    def handle_unauthorized_access():
        """Return 401 JSON envelope for API/JSON calls, redirect to login for browser."""
        if request.path.startswith("/api/") or (
            request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html
        ):
            return make_error_envelope("UNAUTHENTICATED", "Authentication required", 401)
        from flask import redirect, url_for
        return redirect(url_for("auth.login_page", next=request.url))


    @app.errorhandler(400)
    def handle_bad_request(err):
        msg = getattr(err, "description", "Bad Request")
        return make_error_envelope("BAD_REQUEST", str(msg), 400)

    @app.errorhandler(401)
    def handle_unauthorized(err):
        msg = getattr(err, "description", "Authentication required")
        return make_error_envelope("UNAUTHENTICATED", str(msg), 401)

    @app.errorhandler(403)
    def handle_forbidden(err):
        msg = getattr(err, "description", "You do not have permission to access this resource")
        return make_error_envelope("FORBIDDEN", str(msg), 403)

    @app.errorhandler(404)
    def handle_not_found(err):
        msg = getattr(err, "description", "The requested resource was not found")
        return make_error_envelope("NOT_FOUND", str(msg), 404)

    @app.errorhandler(409)
    def handle_conflict(err):
        msg = getattr(err, "description", "Conflict with current state of resource")
        return make_error_envelope("CONFLICT", str(msg), 409)

    @app.errorhandler(422)
    def handle_unprocessable_entity(err):
        msg = getattr(err, "description", "Unable to process the contained instructions")
        return make_error_envelope("VALIDATION_ERROR", str(msg), 422)

    @app.errorhandler(429)
    def handle_too_many_requests(err):
        msg = getattr(err, "description", "Rate limit exceeded")
        return make_error_envelope("RATE_LIMITED", str(msg), 429)

    @app.errorhandler(CSRFError)
    def handle_csrf_error(err):
        return make_error_envelope("CSRF_ERROR", err.description or "CSRF token missing or invalid", 400)

    @app.errorhandler(500)
    @app.errorhandler(Exception)
    def handle_server_error(err):
        if isinstance(err, HTTPException):
            return make_error_envelope(
                err.name.upper().replace(" ", "_"),
                err.description,
                err.code
            )
        app.logger.exception("Unhandled server exception: %s", err)
        return make_error_envelope(
            "SERVER_ERROR",
            "An internal server error occurred.",
            500
        )

    return app


if __name__ == "__main__":
    # Development server only. For prototype demonstration and testing.
    # Production deployment requires a WSGI server (e.g. Waitress / Gunicorn).
    from models import init_db
    application = create_app()
    init_db(application)
    application.run(host="127.0.0.1", port=5000, debug=True)
