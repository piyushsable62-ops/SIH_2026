"""
Blueprint registry for APAAR Nexus.
Imports and registers blueprints, safely guarding unbuilt modules during bootstrap.
"""
import logging

logger = logging.getLogger(__name__)


def register_all(app):
    """Register all application blueprints."""
    from routes.ui_kit import ui_kit_bp
    app.register_blueprint(ui_kit_bp)
    # Health check route (Member A bootstrap)
    try:
        from routes.health import health_bp
        app.register_blueprint(health_bp)
    except (ImportError, AttributeError) as exc:
        logger.debug("health_bp not registered: %s", exc)

    # Authentication & Session (Member A)
    try:
        from routes.auth import auth_bp
        app.register_blueprint(auth_bp)
    except (ImportError, AttributeError) as exc:
        logger.debug("auth_bp not registered: %s", exc)

    # Institutions & Programmes (Member A)
    try:
        from routes.institutions import institutions_bp
        app.register_blueprint(institutions_bp)
    except (ImportError, AttributeError) as exc:
        logger.debug("institutions_bp not registered: %s", exc)

    # Students & Change History (Member A)
    try:
        from routes.students import students_bp
        app.register_blueprint(students_bp)
    except (ImportError, AttributeError) as exc:
        logger.debug("students_bp not registered: %s", exc)

    # Admissions & Seat Ledger (Member A)
    try:
        from routes.admissions import admissions_bp
        app.register_blueprint(admissions_bp)
    except (ImportError, AttributeError) as exc:
        logger.debug("admissions_bp not registered: %s", exc)

    # Identity Resolution & Graph (Member B)
    try:
        from routes.identity import identity_bp
        app.register_blueprint(identity_bp)
    except (ImportError, AttributeError) as exc:
        logger.debug("identity_bp not registered: %s", exc)

    # Risk Engine & Cases (Member B)
    try:
        from routes.risk import risk_bp
        app.register_blueprint(risk_bp)
    except (ImportError, AttributeError) as exc:
        logger.debug("risk_bp not registered: %s", exc)

    # Credentials & Verification (Member B)
    try:
        from routes.credentials import credentials_bp
        app.register_blueprint(credentials_bp)
    except (ImportError, AttributeError) as exc:
        logger.debug("credentials_bp not registered: %s", exc)

    # Consent & Sharing Sessions (Member B)
    try:
        from routes.sharing import sharing_bp
        app.register_blueprint(sharing_bp)
    except (ImportError, AttributeError) as exc:
        logger.debug("sharing_bp not registered: %s", exc)

    # Dashboards (Shared: Member A institution / Member B governance)
    try:
        from routes.dashboards import dashboards_bp
        app.register_blueprint(dashboards_bp)
    except (ImportError, AttributeError) as exc:
        logger.debug("dashboards_bp not registered: %s", exc)

    # Reports (Shared: Member A enrolment / Member B anomalies)
    try:
        from routes.reports import reports_bp
        app.register_blueprint(reports_bp)
    except (ImportError, AttributeError) as exc:
        logger.debug("reports_bp not registered: %s", exc)
