"""Authenticated identity scans and read-only, masked investigation graphs."""

import re
from uuid import UUID
from flask import Blueprint, jsonify, request, render_template
from flask_login import login_required, current_user
from sqlalchemy import select

from extensions import db
from models import RiskCase, StudentRecord, Institution
from routes.risk import api_errors, institution_scope, integer, json_body
from services import identity_resolution, risk_engine
from services.authz import roles_required, scope_query

identity_bp = Blueprint("identity", __name__)


def year(value):
    """Validate optional academic-year text without imposing a new year format."""
    if value is not None and (not isinstance(value, str) or not value.strip() or len(value) > 20):
        raise ValueError("academic_year must be non-empty text of at most 20 characters.")
    return value


def graph_options():
    """Validate graph filters, strict booleans and the node response ceiling."""
    signal = request.args.get("include_signals", "true").lower()
    if signal not in {"true", "false", "1", "0"}:
        raise ValueError("include_signals must be true or false.")
    severity = request.args.get("min_severity")
    if severity is not None and severity not in risk_engine.DEFAULT_BANDS:
        raise ValueError("Invalid min_severity.")
    masked = request.args.get("apaar_masked")
    if masked is not None and re.fullmatch(r"XXXXXXXX\d{4}", masked) is None:
        raise ValueError("apaar_masked must contain XXXXXXXX followed by four digits.")
    search = request.args.get("q")
    if search is not None and (not identity_resolution.normalize_name(search) or len(search) > 150 or re.search(r"\d{12}", search)):
        raise ValueError("Search by student name or use a masked APAAR reference, never a full reference.")
    return {"institution_id": institution_scope(request.args.get("institution_id")),
        "academic_year": year(request.args.get("academic_year")), "case_id": request.args.get("case_id"),
        "apaar_masked": masked, "min_severity": severity, "include_signals": signal in {"true", "1"},
        "limit": integer(request.args.get("limit"), "limit", 300, 1000), "q": search}


@identity_bp.post("/api/identity/scan")
@login_required
@roles_required("AICTE_ADMIN", "INSTITUTION_ADMIN")
@api_errors
def scan():
    """Run evidence and risk evaluation; human review is required for every case."""
    body = json_body({"institution_id", "academic_year"})
    institution = institution_scope(body.get("institution_id"))
    academic_year = year(body.get("academic_year"))
    before = set(db.session.execute(risk_engine.visible_cases().with_only_columns(RiskCase.id)).scalars())
    result = identity_resolution.scan(institution_id=institution, academic_year=academic_year)
    cases = risk_engine.evaluate_all(result)
    created = sum(case.id not in before for case in cases)
    return jsonify(ok=True, data={"scan_id": result.scan_id, "stats": result.stats,
        "cases_created": created, "cases_updated": len(cases) - created, "case_count": len(cases)})


@identity_bp.get("/api/identity/graph")
@login_required
@roles_required("AICTE_ADMIN", "INSTITUTION_ADMIN", "AUDITOR")
@api_errors
def graph():
    """Return scoped, masked graph data; GET never triggers a scan or mutation."""
    return jsonify(ok=True, data=identity_resolution.read_graph(**graph_options()))


@identity_bp.get("/api/identity/node/<node_id>")
@login_required
@roles_required("AICTE_ADMIN", "INSTITUTION_ADMIN", "AUDITOR")
@api_errors
def node(node_id):
    """Return only visible neighboring records, explained edges and authorized cases."""
    try:
        kind, id_ = node_id.split(":", 1)
        UUID(id_)
    except (ValueError, AttributeError):
        raise ValueError("node_id must be a typed UUID from the graph response.") from None
    if kind not in {"student", "institution", "apaar", "contact", "fee_payer"}:
        raise ValueError("Unknown node type.")
    options = graph_options()
    view = identity_resolution.read_graph(**options, focus=node_id)
    selected = next((item for item in view["nodes"] if item["id"] == node_id), None)
    if selected is None:
        raise LookupError("Graph node not found.")
    from models import StudentRecord
    from services.authz import scope_query
    ids = [item["id"].split(":", 1)[1] for item in view["nodes"] if item["type"] == "STUDENT"]
    records = db.session.execute(scope_query(select(StudentRecord), StudentRecord).where(StudentRecord.id.in_(ids))).scalars()
    case_ids = {id_ for item in view["nodes"] for id_ in item["meta"].get("case_ids", [])}
    cases = db.session.execute(risk_engine.visible_cases().where(RiskCase.id.in_(case_ids))).scalars()
    return jsonify(ok=True, data={"node": selected, "linked_records": [risk_engine.public_record(row) for row in records],
        "institutions": [item for item in view["nodes"] if item["type"] == "INSTITUTION"],
        "edges": view["edges"], "linked_cases": [risk_engine._clean(row.to_dict()) for row in cases], "truncated": view["truncated"]})


@identity_bp.get("/identity-graph")
@login_required
@roles_required("AICTE_ADMIN", "INSTITUTION_ADMIN", "AUDITOR")
@api_errors
def explorer():
    """Render the accessible synthetic graph explorer; GET never starts a scan."""
    case_id = request.args.get("case_id")
    case = risk_engine.get_case(case_id) if case_id else None
    years = db.session.execute(scope_query(select(StudentRecord.academic_year), StudentRecord).distinct().order_by(StudentRecord.academic_year.desc())).scalars().all()
    institutions = db.session.execute(scope_query(select(Institution), Institution).order_by(Institution.code)).scalars().all() if current_user.role == "AICTE_ADMIN" else []
    return render_template("identity_graph.html", academic_years=years, institutions=institutions,
        focused_case=case, can_scan=current_user.role in {"AICTE_ADMIN", "INSTITUTION_ADMIN"})
