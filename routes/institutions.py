"""
Institutions, Programmes, Intake Capacities, and Policies routes for APAAR Nexus.
Task A5 implementation: management APIs and UI views.
"""
import re
from typing import Optional, Dict, Any, List
from flask import Blueprint, request, jsonify, render_template, abort
from flask_login import login_required, current_user
from sqlalchemy import or_

from extensions import db
from models.institution import Institution, Programme, IntakeCapacity
from models.student import StudentRecord
from models.policy import SystemPolicy
from models.enums import Role, Classification, EnrolmentStatus
from services.authz import roles_required, scope_query, assert_same_institution
from services.audit_service import log as audit_log

institutions_bp = Blueprint("institutions", __name__)


def make_error(code: str, message: str, status_code: int = 400, details: Optional[Dict[str, Any]] = None):
    """Standardized JSON error envelope."""
    return jsonify({
        "ok": False,
        "error": {
            "code": code,
            "message": message,
            "details": details or {}
        }
    }), status_code


# ---------------------------------------------------------------------------
# HTML Views
# ---------------------------------------------------------------------------

@institutions_bp.route("/institutions", methods=["GET"])
@login_required
@roles_required(Role.AICTE_ADMIN.value, Role.INSTITUTION_ADMIN.value, Role.AUDITOR.value)
def institutions_page():
    """Render the institution directory and management page."""
    is_aicte_admin = (current_user.role == Role.AICTE_ADMIN.value)
    return render_template(
        "institutions.html",
        is_aicte_admin=is_aicte_admin,
        current_institution_id=current_user.institution_id,
    )


@institutions_bp.route("/policies", methods=["GET"])
@login_required
@roles_required(Role.AICTE_ADMIN.value)
def policies_page():
    """Render the system policy management page (AICTE Admin only)."""
    policies = SystemPolicy.query.order_by(SystemPolicy.key).all()
    return render_template(
        "policies.html",
        policies=policies,
    )


# ---------------------------------------------------------------------------
# Institutions API
# ---------------------------------------------------------------------------

@institutions_bp.route("/api/institutions", methods=["GET"])
@login_required
@roles_required(Role.AICTE_ADMIN.value, Role.INSTITUTION_ADMIN.value, Role.AUDITOR.value)
def list_institutions():
    """
    List institutions with search and filtering.
    AICTE_ADMIN and AUDITOR see all; INSTITUTION_ADMIN sees only their assigned institution.
    """
    query = scope_query(Institution.query, Institution)

    q = request.args.get("q", "").strip()
    if q:
        search_pattern = f"%{q}%"
        query = query.filter(
            or_(
                Institution.name.ilike(search_pattern),
                Institution.code.ilike(search_pattern),
                Institution.city.ilike(search_pattern)
            )
        )

    state = request.args.get("state", "").strip()
    if state:
        query = query.filter(Institution.state == state)

    status = request.args.get("status", "").strip().upper()
    if status:
        query = query.filter(Institution.status == status)

    # Pagination
    try:
        page = max(1, int(request.args.get("page", 1)))
        per_page = min(100, max(1, int(request.args.get("per_page", 25))))
    except ValueError:
        page = 1
        per_page = 25

    total = query.count()
    items = query.order_by(Institution.name.asc()).offset((page - 1) * per_page).limit(per_page).all()

    data = []
    for inst in items:
        inst_dict = inst.to_dict()
        inst_dict["programmes_count"] = inst.programmes.count()
        inst_dict["total_regular_capacity"] = sum(c.approved_regular_intake for c in inst.intake_capacities)
        inst_dict["total_supernumerary_capacity"] = sum(c.supernumerary_capacity for c in inst.intake_capacities)
        data.append(inst_dict)

    return jsonify({
        "ok": True,
        "data": data,
        "meta": {
            "page": page,
            "per_page": per_page,
            "total": total
        }
    }), 200


@institutions_bp.route("/api/institutions", methods=["POST"])
@login_required
@roles_required(Role.AICTE_ADMIN.value)
def create_institution():
    """Create a new institution (AICTE Super Admin only)."""
    payload = request.get_json(silent=True) or {}

    code = (payload.get("code") or "").strip().upper()
    name = (payload.get("name") or "").strip()
    state = (payload.get("state") or "Maharashtra").strip()
    city = (payload.get("city") or "Mumbai").strip()
    status = (payload.get("status") or "ACTIVE").strip().upper()

    if not code or not name:
        return make_error("VALIDATION_ERROR", "Institution code and name are required.", 422)

    existing = Institution.query.filter_by(code=code).first()
    if existing:
        return make_error("CONFLICT", f"An institution with code '{code}' already exists.", 409)

    inst = Institution(
        code=code,
        name=name,
        state=state,
        city=city,
        status=status,
        is_synthetic=True
    )
    db.session.add(inst)
    db.session.commit()

    audit_log(
        actor=current_user.username,
        action="INSTITUTION_CREATE",
        entity_type="INSTITUTION",
        entity_id=inst.id,
        details={"code": inst.code, "name": inst.name, "city": inst.city, "state": inst.state},
        institution_id=inst.id
    )

    return jsonify({
        "ok": True,
        "data": inst.to_dict()
    }), 201


@institutions_bp.route("/api/institutions/<institution_id>", methods=["GET"])
@login_required
@roles_required(Role.AICTE_ADMIN.value, Role.INSTITUTION_ADMIN.value, Role.AUDITOR.value)
def get_institution(institution_id: str):
    """Get single institution details including programmes and intake summary."""
    inst = db.session.get(Institution, institution_id)
    if not inst:
        return make_error("NOT_FOUND", "Institution not found.", 404)

    assert_same_institution(inst)

    programmes_data = []
    for prog in inst.programmes.order_by(Programme.code.asc()):
        p_dict = prog.to_dict()
        p_dict["capacities"] = [c.to_dict() for c in prog.capacities.order_by(IntakeCapacity.academic_year.desc())]
        programmes_data.append(p_dict)

    intake_summary = {
        "total_approved_regular": sum(c.approved_regular_intake for c in inst.intake_capacities),
        "total_supernumerary": sum(c.supernumerary_capacity for c in inst.intake_capacities),
        "total_active_students": StudentRecord.query.filter(
            StudentRecord.institution_id == inst.id,
            StudentRecord.enrolment_status != EnrolmentStatus.WITHDRAWN.value
        ).count()
    }

    return jsonify({
        "ok": True,
        "data": {
            "institution": inst.to_dict(),
            "programmes": programmes_data,
            "intake_summary": intake_summary
        }
    }), 200


@institutions_bp.route("/api/institutions/<institution_id>", methods=["PATCH"])
@login_required
@roles_required(Role.AICTE_ADMIN.value, Role.INSTITUTION_ADMIN.value)
def update_institution(institution_id: str):
    """
    Update institution attributes.
    AICTE_ADMIN can update any field; INSTITUTION_ADMIN can only update limited fields of own institution.
    """
    inst = db.session.get(Institution, institution_id)
    if not inst:
        return make_error("NOT_FOUND", "Institution not found.", 404)

    user_role = current_user.role.value if hasattr(current_user.role, "value") else str(current_user.role)
    payload = request.get_json(silent=True) or {}

    old_data = inst.to_dict()

    if user_role == Role.INSTITUTION_ADMIN.value:
        if inst.id != current_user.institution_id:
            return make_error("FORBIDDEN", "Cross-institution access denied.", 403)

        # Restricted fields check
        disallowed_fields = {"code", "name", "status", "is_synthetic", "id"}
        if any(k in payload for k in disallowed_fields):
            return make_error(
                "FORBIDDEN",
                "Institution administrators can only update location and contact fields (city, state). Contact AICTE to modify name or code.",
                403
            )

        if "city" in payload and payload["city"]:
            inst.city = str(payload["city"]).strip()
        if "state" in payload and payload["state"]:
            inst.state = str(payload["state"]).strip()

    elif user_role == Role.AICTE_ADMIN.value:
        if "name" in payload and payload["name"]:
            inst.name = str(payload["name"]).strip()
        if "code" in payload and payload["code"]:
            new_code = str(payload["code"]).strip().upper()
            if new_code != inst.code:
                conflict = Institution.query.filter(Institution.code == new_code, Institution.id != inst.id).first()
                if conflict:
                    return make_error("CONFLICT", f"An institution with code '{new_code}' already exists.", 409)
                inst.code = new_code
        if "city" in payload and payload["city"]:
            inst.city = str(payload["city"]).strip()
        if "state" in payload and payload["state"]:
            inst.state = str(payload["state"]).strip()
        if "status" in payload and payload["status"]:
            inst.status = str(payload["status"]).strip().upper()

    db.session.commit()

    audit_log(
        actor=current_user.username,
        action="INSTITUTION_UPDATE",
        entity_type="INSTITUTION",
        entity_id=inst.id,
        details={"old": old_data, "new": inst.to_dict()},
        institution_id=inst.id
    )

    return jsonify({
        "ok": True,
        "data": inst.to_dict()
    }), 200


# ---------------------------------------------------------------------------
# Programmes API
# ---------------------------------------------------------------------------

@institutions_bp.route("/api/institutions/<institution_id>/programmes", methods=["GET"])
@login_required
@roles_required(Role.AICTE_ADMIN.value, Role.INSTITUTION_ADMIN.value, Role.AUDITOR.value)
def list_programmes(institution_id: str):
    """List all programmes offered by an institution."""
    inst = db.session.get(Institution, institution_id)
    if not inst:
        return make_error("NOT_FOUND", "Institution not found.", 404)

    assert_same_institution(inst)

    progs = inst.programmes.order_by(Programme.code.asc()).all()
    return jsonify({
        "ok": True,
        "data": [p.to_dict() for p in progs]
    }), 200


@institutions_bp.route("/api/institutions/<institution_id>/programmes", methods=["POST"])
@login_required
@roles_required(Role.AICTE_ADMIN.value)
def create_programme(institution_id: str):
    """Create a new degree programme for an institution (AICTE Admin only)."""
    inst = db.session.get(Institution, institution_id)
    if not inst:
        return make_error("NOT_FOUND", "Institution not found.", 404)

    payload = request.get_json(silent=True) or {}
    code = (payload.get("code") or "").strip().upper()
    name = (payload.get("name") or "").strip()
    level = (payload.get("level") or "UG").strip().upper()

    if not code or not name:
        return make_error("VALIDATION_ERROR", "Programme code and name are required.", 422)

    existing = Programme.query.filter_by(institution_id=inst.id, code=code).first()
    if existing:
        return make_error("CONFLICT", f"Programme '{code}' already exists for this institution.", 409)

    prog = Programme(
        institution_id=inst.id,
        code=code,
        name=name,
        level=level
    )
    db.session.add(prog)
    db.session.commit()

    audit_log(
        actor=current_user.username,
        action="PROGRAMME_CREATE",
        entity_type="PROGRAMME",
        entity_id=prog.id,
        details={"code": prog.code, "name": prog.name, "level": prog.level},
        institution_id=inst.id
    )

    return jsonify({
        "ok": True,
        "data": prog.to_dict()
    }), 201


@institutions_bp.route("/api/institutions/<institution_id>/programmes/<programme_id>", methods=["PATCH"])
@login_required
@roles_required(Role.AICTE_ADMIN.value)
def update_programme(institution_id: str, programme_id: str):
    """Update programme metadata (AICTE Admin only)."""
    inst = db.session.get(Institution, institution_id)
    if not inst:
        return make_error("NOT_FOUND", "Institution not found.", 404)

    prog = Programme.query.filter_by(id=programme_id, institution_id=inst.id).first()
    if not prog:
        return make_error("NOT_FOUND", "Programme not found for this institution.", 404)

    payload = request.get_json(silent=True) or {}
    old_data = prog.to_dict()

    if "name" in payload and payload["name"]:
        prog.name = str(payload["name"]).strip()
    if "level" in payload and payload["level"]:
        prog.level = str(payload["level"]).strip().upper()
    if "code" in payload and payload["code"]:
        new_code = str(payload["code"]).strip().upper()
        if new_code != prog.code:
            conflict = Programme.query.filter(
                Programme.institution_id == inst.id,
                Programme.code == new_code,
                Programme.id != prog.id
            ).first()
            if conflict:
                return make_error("CONFLICT", f"Programme '{new_code}' already exists for this institution.", 409)
            prog.code = new_code

    db.session.commit()

    audit_log(
        actor=current_user.username,
        action="PROGRAMME_UPDATE",
        entity_type="PROGRAMME",
        entity_id=prog.id,
        details={"old": old_data, "new": prog.to_dict()},
        institution_id=inst.id
    )

    return jsonify({
        "ok": True,
        "data": prog.to_dict()
    }), 200


# ---------------------------------------------------------------------------
# Intake Capacities API
# ---------------------------------------------------------------------------

@institutions_bp.route("/api/institutions/<institution_id>/capacities", methods=["GET"])
@login_required
@roles_required(Role.AICTE_ADMIN.value, Role.INSTITUTION_ADMIN.value, Role.AUDITOR.value)
def list_capacities(institution_id: str):
    """List all intake capacities for an institution."""
    inst = db.session.get(Institution, institution_id)
    if not inst:
        return make_error("NOT_FOUND", "Institution not found.", 404)

    assert_same_institution(inst)

    capacities = inst.intake_capacities.order_by(IntakeCapacity.academic_year.desc()).all()
    return jsonify({
        "ok": True,
        "data": [c.to_dict() for c in capacities]
    }), 200


@institutions_bp.route("/api/institutions/<institution_id>/capacities", methods=["POST"])
@login_required
@roles_required(Role.AICTE_ADMIN.value)
def create_capacity(institution_id: str):
    """Configure annual intake capacity for a programme (AICTE Admin only)."""
    inst = db.session.get(Institution, institution_id)
    if not inst:
        return make_error("NOT_FOUND", "Institution not found.", 404)

    payload = request.get_json(silent=True) or {}
    programme_id = (payload.get("programme_id") or "").strip()
    academic_year = (payload.get("academic_year") or "").strip()

    if not programme_id or not academic_year:
        return make_error("VALIDATION_ERROR", "programme_id and academic_year are required.", 422)

    prog = Programme.query.filter_by(id=programme_id, institution_id=inst.id).first()
    if not prog:
        return make_error("NOT_FOUND", "Programme not found for this institution.", 404)

    approved_intake = payload.get("approved_regular_intake", payload.get("approved_intake", 120))
    supernumerary = payload.get("supernumerary_capacity", payload.get("supernumerary_quota", 10))
    category_rules = payload.get("category_rules")

    try:
        approved_intake = int(approved_intake)
        supernumerary = int(supernumerary)
    except (ValueError, TypeError):
        return make_error("VALIDATION_ERROR", "Intake values must be integers.", 422)

    if approved_intake < 0 or supernumerary < 0:
        return make_error("VALIDATION_ERROR", "Approved intake and supernumerary capacity cannot be negative.", 422)

    existing = IntakeCapacity.query.filter_by(
        institution_id=inst.id,
        programme_id=prog.id,
        academic_year=academic_year
    ).first()
    if existing:
        return make_error("CONFLICT", f"Capacity already configured for {prog.code} in {academic_year}.", 409)

    cap = IntakeCapacity(
        institution_id=inst.id,
        programme_id=prog.id,
        academic_year=academic_year,
        approved_regular_intake=approved_intake,
        supernumerary_capacity=supernumerary,
        category_rules=category_rules
    )
    db.session.add(cap)
    db.session.commit()

    audit_log(
        actor=current_user.username,
        action="CAPACITY_CREATE",
        entity_type="INTAKE_CAPACITY",
        entity_id=cap.id,
        details={
            "programme": prog.code,
            "academic_year": academic_year,
            "approved_regular_intake": approved_intake,
            "supernumerary_capacity": supernumerary
        },
        institution_id=inst.id
    )

    return jsonify({
        "ok": True,
        "data": cap.to_dict()
    }), 201


@institutions_bp.route("/api/institutions/<institution_id>/capacities/<capacity_id>", methods=["PATCH"])
@login_required
@roles_required(Role.AICTE_ADMIN.value)
def update_capacity(institution_id: str, capacity_id: str):
    """
    Update intake capacity.
    Validates: approved intake >= 0, supernumerary >= 0.
    Enforces: cannot reduce approved intake below current active regular admissions.
    """
    inst = db.session.get(Institution, institution_id)
    if not inst:
        return make_error("NOT_FOUND", "Institution not found.", 404)

    cap = IntakeCapacity.query.filter_by(id=capacity_id, institution_id=inst.id).first()
    if not cap:
        return make_error("NOT_FOUND", "Intake capacity record not found.", 404)

    payload = request.get_json(silent=True) or {}
    old_data = cap.to_dict()

    new_regular = payload.get("approved_regular_intake", payload.get("approved_intake"))
    new_super = payload.get("supernumerary_capacity", payload.get("supernumerary_quota"))
    new_rules = payload.get("category_rules")

    if new_regular is not None:
        try:
            new_regular = int(new_regular)
        except (ValueError, TypeError):
            return make_error("VALIDATION_ERROR", "Approved intake must be an integer.", 422)

        if new_regular < 0:
            return make_error("VALIDATION_ERROR", "Approved intake cannot be negative.", 422)

        # Capacity guard: check current active regular admissions
        current_regular_admissions = StudentRecord.query.filter(
            StudentRecord.institution_id == inst.id,
            StudentRecord.programme_id == cap.programme_id,
            StudentRecord.academic_year == cap.academic_year,
            StudentRecord.classification == Classification.REGULAR.value,
            StudentRecord.enrolment_status != EnrolmentStatus.WITHDRAWN.value
        ).count()

        if new_regular < current_regular_admissions:
            prog = db.session.get(Programme, cap.programme_id)
            prog_code = prog.code if prog else "Programme"
            return make_error(
                "CONFLICT",
                f"Cannot reduce approved intake to {new_regular} because there are currently {current_regular_admissions} active regular admissions in {prog_code} ({cap.academic_year}).",
                409,
                details={
                    "current_admissions": current_regular_admissions,
                    "requested_intake": new_regular
                }
            )

        cap.approved_regular_intake = new_regular

    if new_super is not None:
        try:
            new_super = int(new_super)
        except (ValueError, TypeError):
            return make_error("VALIDATION_ERROR", "Supernumerary capacity must be an integer.", 422)

        if new_super < 0:
            return make_error("VALIDATION_ERROR", "Supernumerary capacity cannot be negative.", 422)

        cap.supernumerary_capacity = new_super

    if new_rules is not None:
        cap.category_rules = new_rules

    db.session.commit()

    audit_log(
        actor=current_user.username,
        action="CAPACITY_UPDATE",
        entity_type="INTAKE_CAPACITY",
        entity_id=cap.id,
        details={"old": old_data, "new": cap.to_dict()},
        institution_id=inst.id
    )

    return jsonify({
        "ok": True,
        "data": cap.to_dict()
    }), 200


# ---------------------------------------------------------------------------
# System Policies API (AICTE Admin Only)
# ---------------------------------------------------------------------------

@institutions_bp.route("/api/policies", methods=["GET"])
@login_required
@roles_required(Role.AICTE_ADMIN.value)
def list_policies():
    """List all system policies (AICTE Super Admin only)."""
    policies = SystemPolicy.query.order_by(SystemPolicy.key.asc()).all()
    return jsonify({
        "ok": True,
        "data": [p.to_dict() for p in policies]
    }), 200


@institutions_bp.route("/api/policies/<key>", methods=["PATCH"])
@login_required
@roles_required(Role.AICTE_ADMIN.value)
def update_policy(key: str):
    """
    Update a system policy with strict shape validation.
    - capacity_warning_thresholds: ascending list of ints <= 100
    - risk_weights: dict with non-negative int values
    - sharing_allowed_expiry_hours: list of ints subset of {24, 72, 168}
    - apaar_regex: valid compileable regex string
    """
    policy = SystemPolicy.query.filter_by(key=key).first()
    if not policy:
        return make_error("NOT_FOUND", f"System policy '{key}' was not found.", 404)

    payload = request.get_json(silent=True) or {}
    if "value" not in payload:
        return make_error("VALIDATION_ERROR", "Missing 'value' field in payload.", 422)

    val = payload["value"]
    old_value = policy.value

    # Validate value shapes according to policy key
    if key == "capacity_warning_thresholds":
        if not isinstance(val, list) or len(val) == 0:
            return make_error("VALIDATION_ERROR", "Thresholds must be a non-empty list of integers.", 422)
        for item in val:
            if type(item) is not int or item < 0 or item > 100:
                return make_error("VALIDATION_ERROR", "All thresholds must be integers between 0 and 100.", 422)
        # Check ascending order
        for i in range(len(val) - 1):
            if val[i] >= val[i + 1]:
                return make_error("VALIDATION_ERROR", "Thresholds must be in strictly ascending order.", 422)

    elif key == "risk_weights":
        if not isinstance(val, dict):
            return make_error("VALIDATION_ERROR", "Risk weights must be an object/dict.", 422)
        for weight_name, weight_val in val.items():
            if type(weight_val) is not int or weight_val < 0:
                return make_error("VALIDATION_ERROR", f"Weight for '{weight_name}' must be a non-negative integer.", 422)

    elif key in ("sharing_allowed_expiry_hours", "sharing_expiry_options_hours", "expiry"):
        if not isinstance(val, list):
            return make_error("VALIDATION_ERROR", "Expiry options must be a list of integers.", 422)
        allowed_set = {24, 72, 168}
        for item in val:
            if type(item) is not int or item not in allowed_set:
                return make_error("VALIDATION_ERROR", f"Expiry hours must only contain values from {sorted(list(allowed_set))}.", 422)

    elif key == "apaar_regex":
        if not isinstance(val, str) or not val.strip():
            return make_error("VALIDATION_ERROR", "APAAR regex must be a non-empty string.", 422)
        try:
            re.compile(val)
        except re.error as err:
            return make_error("VALIDATION_ERROR", f"Invalid regular expression: {str(err)}", 422)

    # Save policy update
    policy.value = val
    policy.updated_by = current_user.username
    db.session.commit()

    audit_log(
        actor=current_user.username,
        action="POLICY_UPDATE",
        entity_type="SYSTEM_POLICY",
        entity_id=policy.key,
        details={"old_value": old_value, "new_value": val},
        institution_id=None
    )

    return jsonify({
        "ok": True,
        "data": policy.to_dict()
    }), 200
