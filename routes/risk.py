"""Investigation API. Human review never changes students or credentials."""

from functools import wraps
from flask import Blueprint, jsonify, request
from flask_login import login_required, current_user
from sqlalchemy import func, or_, select

from extensions import db
from models import Institution, RiskCase
from services import risk_engine
from services.authz import roles_required, scope_query

risk_bp = Blueprint("risk", __name__, url_prefix="/api/risk")
READ_ROLES = ("AICTE_ADMIN", "INSTITUTION_ADMIN", "AUDITOR")


def api_errors(fn):
    """Translate expected service failures into the shared envelope."""
    @wraps(fn)
    def wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except risk_engine.WorkflowConflict as exc:
            return jsonify(ok=False, error={"code": "CONFLICT", "message": str(exc),
                "details": {"allowed_next_actions": exc.allowed}}), 409
        except (ValueError, PermissionError, LookupError) as exc:
            status, code = (403, "FORBIDDEN") if isinstance(exc, PermissionError) else ((404, "NOT_FOUND") if isinstance(exc, LookupError) else (422, "VALIDATION_ERROR"))
            return jsonify(ok=False, error={"code": code, "message": str(exc), "details": {}}), status
    return wrapped


def json_body(allowed: set[str]) -> dict:
    """Require a JSON object and reject unknown fields rather than ignore mistakes."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or set(body) - allowed:
        raise ValueError("A JSON object containing only the documented fields is required.")
    return body


def integer(value: str, name: str, default: int, maximum: int) -> int:
    """Validate bounded positive integer query parameters."""
    try:
        result = int(value) if value is not None else default
    except (ValueError, TypeError):
        raise ValueError(f"{name} must be a positive integer.") from None
    if not 1 <= result <= maximum:
        raise ValueError(f"{name} must be between 1 and {maximum}.")
    return result


def institution_scope(value: str | None) -> str | None:
    """Check an optional institution filter without revealing foreign institutions."""
    if current_user.role == "INSTITUTION_ADMIN":
        if value is not None and value != current_user.institution_id:
            raise PermissionError("Only your own institution can be accessed.")
        value = current_user.institution_id
    if value is not None:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("institution_id must be a non-empty string.")
        if db.session.execute(scope_query(select(Institution.id), Institution).where(Institution.id == value)).first() is None:
            raise LookupError("Institution not found.")
    return value


@risk_bp.get("/cases")
@login_required
@roles_required(*READ_ROLES)
@api_errors
def list_cases():
    """List scoped cases in descending score order with stable pagination."""
    query = risk_engine.visible_cases()
    for name, options in (("severity", risk_engine.DEFAULT_BANDS), ("status", risk_engine.TRANSITIONS)):
        value = request.args.get(name)
        if value is not None:
            if value not in options:
                raise ValueError(f"Invalid {name}.")
            query = query.where(getattr(RiskCase, name) == value)
    institution = institution_scope(request.args.get("institution_id"))
    if institution:
        # National users may filter by membership, including multi-institution cases.
        from models import RiskEvidence
        membership = select(RiskEvidence.case_id).where(RiskEvidence.details["_risk"]["affected_institution_ids"].contains(institution))
        query = query.where(or_(RiskCase.institution_id == institution, RiskCase.id.in_(membership)))
    assigned = request.args.get("assigned_to")
    if assigned is not None:
        query = query.where(RiskCase.assigned_to.is_(None) if assigned == "" else RiskCase.assigned_to == assigned)
    text = request.args.get("q")
    if text:
        if len(text) > 200:
            raise ValueError("q must be at most 200 characters.")
        escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.where(or_(RiskCase.reference.ilike(f"%{escaped}%", escape="\\"), RiskCase.summary.ilike(f"%{escaped}%", escape="\\")))
    page = integer(request.args.get("page"), "page", 1, 1000000)
    per_page = integer(request.args.get("per_page"), "per_page", 25, 100)
    total = db.session.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.session.execute(query.order_by(RiskCase.score.desc(), RiskCase.id).offset((page - 1) * per_page).limit(per_page)).scalars()
    return jsonify(ok=True, data=[risk_engine._clean(row.to_dict()) for row in rows],
        meta={"page": page, "per_page": per_page, "total": total, "pages": (total + per_page - 1) // per_page})


@risk_bp.get("/cases/<case_id>")
@login_required
@roles_required(*READ_ROLES)
@api_errors
def detail(case_id):
    """Return a scoped case with masked evidence and complete review history."""
    return jsonify(ok=True, data=risk_engine.case_detail(risk_engine.get_case(case_id)))


@risk_bp.post("/cases/<case_id>/actions")
@login_required
@roles_required("AICTE_ADMIN", "INSTITUTION_ADMIN")
@api_errors
def action(case_id):
    """Apply human case review only; never change a student or revoke a credential."""
    body = json_body({"action", "note", "assigned_to"})
    if not isinstance(body.get("action"), str) or body["action"] not in risk_engine.ACTION_STATUS:
        raise ValueError("Unknown case action.")
    assignment = {"assigned_to": body["assigned_to"]} if "assigned_to" in body else {}
    case = risk_engine.act_on_case(case_id, body["action"], body.get("note"), **assignment)
    return jsonify(ok=True, data=risk_engine.case_detail(case))


@risk_bp.patch("/cases/<case_id>")
@login_required
@roles_required("AICTE_ADMIN")
@api_errors
def assign(case_id):
    """Assign a validated AICTE reviewer without taking any student-level action."""
    body = json_body({"assigned_to"})
    if "assigned_to" not in body:
        raise ValueError("assigned_to is required (null clears the assignment).")
    return jsonify(ok=True, data=risk_engine.case_detail(risk_engine.assign_case(case_id, body["assigned_to"])))
