"""Temporary, database-free visual preview; never available outside debug mode."""

from types import SimpleNamespace

from flask import Blueprint, abort, current_app, request
from flask.blueprints import BlueprintSetupState
from flask_login import AnonymousUserMixin

ui_kit_bp = Blueprint("ui_kit", __name__)
PREVIEW_ROLES = (
    "AICTE_ADMIN", "INSTITUTION_ADMIN", "STUDENT", "VERIFIER", "AUDITOR",
)


@ui_kit_bp.app_context_processor
def shell_routes() -> dict:
    """Expose registered GET paths and endpoints without guessing blueprint names."""
    return {
        "ui_paths": {rule.rule for rule in current_app.url_map.iter_rules() if "GET" in rule.methods},
        "ui_endpoints": set(current_app.view_functions),
    }


def index() -> str:
    """Render synthetic components only; role selection does not authenticate anyone."""
    if not current_app.debug:
        abort(404)
    role = request.args.get("role", "AICTE_ADMIN")
    if role not in (*PREVIEW_ROLES, "ANONYMOUS"):
        abort(422, description="Choose a supported preview role.")
    public = request.args.get("shell", "") == "public" or role == "ANONYMOUS"
    # Template-local fixture: no login_user(), database rows, or session role changes.
    user = AnonymousUserMixin() if role == "ANONYMOUS" else SimpleNamespace(
        is_authenticated=True, username="UI preview", role=role,
    )
    # Render this isolated gallery without Flask-Login's context processor:
    # A0 has no user_loader yet. Never install a fake auth loader or modify g/session.
    return current_app.jinja_env.get_template("ui_kit.html").render(
        current_user=user, preview_role=role, preview_roles=PREVIEW_ROLES,
        preview_public=public, is_ui_preview=True, **shell_routes(),
    )


@ui_kit_bp.record_once
def register_preview(state: BlueprintSetupState) -> None:
    """Omit the route entirely when the factory starts with debug disabled."""
    if state.app.debug:
        state.app.add_url_rule("/ui-kit", endpoint="ui_kit.index", view_func=index, methods=["GET"])
