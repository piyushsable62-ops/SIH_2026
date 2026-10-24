"""Investigation API. Human review never changes students or credentials."""

from functools import wraps
from flask import Blueprint, jsonify, request, render_template
from flask_login import login_required, current_user
from sqlalchemy import func, or_, select

from extensions import db
from models import Institution, RiskCase, RiskEvidence, StudentRecord, User
from services import risk_engine
from services.authz import roles_required, scope_query

risk_bp = Blueprint("risk", __name__)
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


def filtered_cases(facets: bool = True):
    """Share authorized list/summary filters, including scoped student-name search."""
    query = risk_engine.visible_cases()
    for name, options in (("severity", risk_engine.DEFAULT_BANDS), ("status", risk_engine.TRANSITIONS)):
        value = request.args.get(name)
        if value is not None:
            if value not in options:
                raise ValueError(f"Invalid {name}.")
            if facets:
                query = query.where(getattr(RiskCase, name) == value)
    institution = institution_scope(request.args.get("institution_id"))
    if institution:
        # National users may filter by membership, including multi-institution cases.
        membership = select(RiskEvidence.case_id).where(RiskEvidence.details["_risk"]["affected_institution_ids"].contains(institution))
        query = query.where(or_(RiskCase.institution_id == institution, RiskCase.id.in_(membership)))
    assigned = request.args.get("assigned_to")
    if assigned is not None:
        query = query.where(RiskCase.assigned_to.is_(None) if assigned in {"", "__unassigned__"} else RiskCase.assigned_to == assigned)
    text = request.args.get("q")
    if text:
        if len(text) > 200:
            raise ValueError("q must be at most 200 characters.")
        escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        from services.identity_resolution import normalize_name
        normalized = normalize_name(text)
        names = scope_query(select(1).select_from(RiskEvidence).join(StudentRecord,
            RiskEvidence.related_student_ids.contains(StudentRecord.id)), StudentRecord).where(
                RiskEvidence.case_id == RiskCase.id,
                StudentRecord.normalized_name.contains(normalized, autoescape=True) if normalized else False).exists()
        query = query.where(or_(RiskCase.reference.ilike(f"%{escaped}%", escape="\\"), RiskCase.summary.ilike(f"%{escaped}%", escape="\\"), names))
    return query


def case_rows(cases: list[RiskCase]) -> list[dict]:
    """Add authorized institution labels to compact case rows without history payloads."""
    by_case = {case.id: ({case.institution_id} if case.institution_id else set()) for case in cases}
    for row in db.session.execute(select(RiskEvidence).where(RiskEvidence.case_id.in_(by_case))).scalars():
        by_case[row.case_id].update((row.details or {}).get("_risk", {}).get("affected_institution_ids", []))
    all_ids = set().union(*by_case.values()) if by_case else set()
    institutions = {row.id: {"id": row.id, "code": row.code, "name": row.name} for row in db.session.execute(
        scope_query(select(Institution), Institution).where(Institution.id.in_(all_ids))).scalars()}
    return [{**risk_engine._clean(case.to_dict()), "institutions": [institutions[id_] for id_ in sorted(by_case[case.id]) if id_ in institutions]} for case in cases]


@risk_bp.get("/api/risk/cases")
@login_required
@roles_required(*READ_ROLES)
@api_errors
def list_cases():
    """List scoped cases in descending score order with stable pagination."""
    query = filtered_cases()
    page = integer(request.args.get("page"), "page", 1, 1000000)
    per_page = integer(request.args.get("per_page"), "per_page", 25, 100)
    total = db.session.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.session.execute(query.order_by(RiskCase.score.desc(), RiskCase.id).offset((page - 1) * per_page).limit(per_page)).scalars().all()
    return jsonify(ok=True, data=case_rows(rows),
        meta={"page": page, "per_page": per_page, "total": total, "pages": (total + per_page - 1) // per_page})


@risk_bp.get("/api/risk/cases/<case_id>")
@login_required
@roles_required(*READ_ROLES)
@api_errors
def detail(case_id):
    """Return a scoped case with masked evidence and complete review history."""
    return jsonify(ok=True, data=risk_engine.case_detail(risk_engine.get_case(case_id)))


@risk_bp.post("/api/risk/cases/<case_id>/actions")
@login_required
@roles_required("AICTE_ADMIN", "INSTITUTION_ADMIN")
@api_errors
def action(case_id):
    """Apply human case review only; never change a student or revoke a credential."""
    body = json_body({"action", "note", "assigned_to"})
    if not isinstance(body.get("action"), str) or body["action"] not in {*risk_engine.ACTION_STATUS, "INSTITUTION_RESPONSE"}:
        raise ValueError("Unknown case action.")
    assignment = {"assigned_to": body["assigned_to"]} if "assigned_to" in body else {}
    case = risk_engine.act_on_case(case_id, body["action"], body.get("note"), **assignment)
    return jsonify(ok=True, data=risk_engine.case_detail(case))


@risk_bp.patch("/api/risk/cases/<case_id>")
@login_required
@roles_required("AICTE_ADMIN")
@api_errors
def assign(case_id):
    """Assign a validated AICTE reviewer without taking any student-level action."""
    body = json_body({"assigned_to"})
    if "assigned_to" not in body:
        raise ValueError("assigned_to is required (null clears the assignment).")
    return jsonify(ok=True, data=risk_engine.case_detail(risk_engine.assign_case(case_id, body["assigned_to"])))


@risk_bp.get("/api/risk/summary")
@login_required
@roles_required(*READ_ROLES)
@api_errors
def summary():
    """Count the complete authorized context, not just the current paginated rows."""
    context = filtered_cases(facets=False).subquery()
    severities = {name: 0 for name in reversed(risk_engine.DEFAULT_BANDS)}
    statuses = {name: 0 for name in risk_engine.TRANSITIONS}
    severities.update(dict(db.session.execute(select(context.c.severity, func.count()).group_by(context.c.severity)).all()))
    statuses.update(dict(db.session.execute(select(context.c.status, func.count()).group_by(context.c.status)).all()))
    return jsonify(ok=True, data={"severity": severities, "status": statuses, "total": sum(severities.values())})


@risk_bp.get("/api/risk/reviewers")
@login_required
@roles_required(*READ_ROLES)
def reviewers():
    """Expose only active AICTE reviewer usernames/names, never credentials or extra accounts."""
    users = db.session.execute(select(User).where(User.role == "AICTE_ADMIN", User.is_active == True).order_by(User.username)).scalars()
    return jsonify(ok=True, data=[{"username": user.username, "full_name": user.full_name} for user in users])


@risk_bp.get("/risk-cases")
@login_required
@roles_required(*READ_ROLES)
def case_list_page():
    """Render the simple investigation list; mutation controls are absent for auditors."""
    institutions = db.session.execute(scope_query(select(Institution), Institution).order_by(Institution.code)).scalars().all() if current_user.role == "AICTE_ADMIN" else []
    return render_template("risk_cases.html", institutions=institutions,
        severities=list(reversed(risk_engine.DEFAULT_BANDS)), statuses=list(risk_engine.TRANSITIONS))


@risk_bp.get("/risk-cases/<case_id>")
@login_required
@roles_required(*READ_ROLES)
@api_errors
def case_detail_page(case_id):
    """Authorize the page before returning any case identifier or rendering actions."""
    case = risk_engine.get_case(case_id)
    return render_template("risk_case_detail.html", case=case,
        can_decide=current_user.role == "AICTE_ADMIN", institution_user=current_user.role == "INSTITUTION_ADMIN")
