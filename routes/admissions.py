"""
Admissions and Dynamic Seat Ledger routes for APAAR Nexus (Task A10).
Smart India Hackathon Problem Statement PS 26263.

Provides:
- GET /seat-ledger — HTML interface for Dynamic Seat Ledger, precheck, and admission history.
- GET /api/seats/summary — Scope-aware list of programme summaries.
- GET /api/seats/ledger — Detailed per-programme ledger with category breakdown.
- POST /api/admissions/precheck — Simulation pre-check (allowed/blocked + projected capacity).
- POST /api/admissions — Commit admission via seat_ledger.commit_admission (201 / 409).
- GET /api/admissions/events — Paginated admission history including rejected attempts.
"""
from datetime import datetime, date
from flask import Blueprint, request, render_template, current_app, jsonify
from flask_login import login_required, current_user

from extensions import db
from models.institution import Institution, Programme, IntakeCapacity
from models.student import StudentRecord
from models.admission import AdmissionEvent
from models.enums import (
    Role,
    Classification,
    AdmissionCategory,
    AdmissionEventResult,
)
from services import seat_ledger
from services import audit_service
from services.apaar_adapter import mask_apaar

admissions_bp = Blueprint("admissions", __name__)


def _make_error_response(code: str, message: str, details: dict = None, status_code: int = 400):
    """Standard error envelope per AGENTS.md §5.6."""
    return jsonify({
        "ok": False,
        "error": {
            "code": code,
            "message": message,
            "details": details or {}
        }
    }), status_code


# ==============================================================================
# HTML Page View
# ==============================================================================

@admissions_bp.route("/seat-ledger", methods=["GET"])
@login_required
def seat_ledger_page():
    """Renders the Dynamic Seat Ledger page (AICTE_ADMIN, INSTITUTION_ADMIN, AUDITOR)."""
    if current_user.role not in (Role.AICTE_ADMIN.value, Role.INSTITUTION_ADMIN.value, Role.AUDITOR.value):
        return _make_error_response("FORBIDDEN", "You do not have permission to view the Seat Ledger.", status_code=403)

    is_aicte_admin = (current_user.role in (Role.AICTE_ADMIN.value, Role.AUDITOR.value))
    user_inst_id = current_user.institution_id if current_user.role == Role.INSTITUTION_ADMIN.value else None

    # Load institutions for filter dropdown if AICTE_ADMIN
    institutions = []
    if is_aicte_admin:
        institutions = Institution.query.filter_by(status="ACTIVE").order_by(Institution.name).all()
    elif user_inst_id:
        inst = Institution.query.get(user_inst_id)
        if inst:
            institutions = [inst]

    return render_template(
        "seat_ledger.html",
        is_aicte_admin=is_aicte_admin,
        user_institution_id=user_inst_id,
        institutions=institutions,
        academic_years=["2026-27", "2025-26", "2024-25"],
    )


# ==============================================================================
# API Routes
# ==============================================================================

@admissions_bp.route("/api/seats/summary", methods=["GET"])
@login_required
def get_seats_summary():
    """
    List of programme summaries (scope-aware).
    Query filters: academic_year, institution_id, programme_id.
    """
    if current_user.role not in (Role.AICTE_ADMIN.value, Role.INSTITUTION_ADMIN.value, Role.AUDITOR.value):
        return _make_error_response("FORBIDDEN", "Access denied.", status_code=403)

    req_inst_id = request.args.get("institution_id")
    req_prog_id = request.args.get("programme_id")
    req_year = request.args.get("academic_year") or "2026-27"

    # Multi-tenant scoping: INSTITUTION_ADMIN can only view own institution
    if current_user.role == Role.INSTITUTION_ADMIN.value:
        if req_inst_id and req_inst_id != current_user.institution_id:
            return _make_error_response("FORBIDDEN", "Cannot access other institution's data.", status_code=403)
        inst_id = current_user.institution_id
    else:
        inst_id = req_inst_id

    query = IntakeCapacity.query.join(Programme).join(Institution)

    if inst_id:
        query = query.filter(IntakeCapacity.institution_id == inst_id)
    if req_prog_id:
        query = query.filter(IntakeCapacity.programme_id == req_prog_id)
    if req_year:
        query = query.filter(IntakeCapacity.academic_year == req_year)

    capacities = query.order_by(Institution.name, Programme.name).all()
    summaries = []

    for cap in capacities:
        try:
            s = seat_ledger.get_summary(cap.institution_id, cap.programme_id, cap.academic_year)
            s["programme_code"] = cap.programme.code
            s["programme_name"] = cap.programme.name
            s["programme_level"] = cap.programme.level
            s["institution_code"] = cap.institution.code
            s["institution_name"] = cap.institution.name
            summaries.append(s)
        except seat_ledger.CapacityNotFound:
            continue

    return jsonify({
        "ok": True,
        "data": summaries,
        "meta": {"total": len(summaries)}
    }), 200


@admissions_bp.route("/api/seats/ledger", methods=["GET"])
@login_required
def get_seats_ledger():
    """
    Detailed per-programme ledger with category breakdown and active counts.
    Query filters: academic_year, institution_id, programme_id.
    """
    if current_user.role not in (Role.AICTE_ADMIN.value, Role.INSTITUTION_ADMIN.value, Role.AUDITOR.value):
        return _make_error_response("FORBIDDEN", "Access denied.", status_code=403)

    req_inst_id = request.args.get("institution_id")
    req_prog_id = request.args.get("programme_id")
    req_year = request.args.get("academic_year") or "2026-27"

    # Scoping
    if current_user.role == Role.INSTITUTION_ADMIN.value:
        if req_inst_id and req_inst_id != current_user.institution_id:
            return _make_error_response("FORBIDDEN", "Cannot access other institution's data.", status_code=403)
        inst_id = current_user.institution_id
    else:
        inst_id = req_inst_id

    if not inst_id or not req_prog_id:
        return _make_error_response(
            "VALIDATION_ERROR",
            "Both institution_id and programme_id are required for detailed ledger view.",
            status_code=422
        )

    cap = IntakeCapacity.query.filter_by(
        institution_id=inst_id,
        programme_id=req_prog_id,
        academic_year=req_year
    ).first()

    if not cap:
        return _make_error_response(
            "NOT_FOUND",
            f"No intake capacity found for programme in academic year {req_year}.",
            status_code=404
        )

    summary = seat_ledger.get_summary(inst_id, req_prog_id, req_year)
    summary["programme_code"] = cap.programme.code
    summary["programme_name"] = cap.programme.name
    summary["programme_level"] = cap.programme.level
    summary["institution_code"] = cap.institution.code
    summary["institution_name"] = cap.institution.name
    summary["category_rules"] = cap.category_rules

    return jsonify({
        "ok": True,
        "data": summary
    }), 200


@admissions_bp.route("/api/admissions/precheck", methods=["POST"])
@login_required
def precheck_admission():
    """
    Admission pre-check simulation:
    Evaluates category eligibility, classification pool, and seat limits without writing state.
    Audit-logs the precheck attempt if rejected.
    """
    if current_user.role not in (Role.AICTE_ADMIN.value, Role.INSTITUTION_ADMIN.value):
        return _make_error_response("FORBIDDEN", "Access denied.", status_code=403)

    payload = request.get_json(silent=True) or {}
    institution_id = payload.get("institution_id")

    # Scope check
    if current_user.role == Role.INSTITUTION_ADMIN.value:
        if institution_id and institution_id != current_user.institution_id:
            return _make_error_response("FORBIDDEN", "Cannot run admission precheck for another institution.", status_code=403)
        institution_id = current_user.institution_id
        payload["institution_id"] = institution_id

    if not payload.get("programme_id") or not payload.get("academic_year"):
        return _make_error_response("VALIDATION_ERROR", "programme_id and academic_year are required.", status_code=422)

    result = seat_ledger.precheck(payload)

    # Audit log if rejected
    if not result.get("allowed"):
        audit_service.log(
            actor=current_user.username,
            action="ADMISSION_PRECHECK_REJECTED",
            entity_type="admission_precheck",
            entity_id=payload.get("institution_student_id") or "PRECHECK",
            institution_id=institution_id,
            details={
                "reason_code": result.get("reason_code"),
                "message": result.get("message"),
                "classification": payload.get("classification"),
                "category": payload.get("admission_category") or payload.get("category"),
                "programme_id": payload.get("programme_id"),
                "academic_year": payload.get("academic_year"),
            }
        )

    return jsonify({
        "ok": True,
        "data": result
    }), 200


@admissions_bp.route("/api/admissions", methods=["POST"])
@login_required
def commit_admission_route():
    """
    Commits an admission through the dynamic seat ledger Prevent engine.
    Returns 201 on success; 409 on capacity/duplicate/category conflict.
    """
    if current_user.role not in (Role.AICTE_ADMIN.value, Role.INSTITUTION_ADMIN.value):
        return _make_error_response("FORBIDDEN", "Access denied.", status_code=403)

    payload = request.get_json(silent=True) or {}
    institution_id = payload.get("institution_id")

    # Scope check: Institution admin cannot admit into another institution
    if current_user.role == Role.INSTITUTION_ADMIN.value:
        if institution_id and institution_id != current_user.institution_id:
            return _make_error_response("FORBIDDEN", "Cannot admit students into another institution.", status_code=403)
        institution_id = current_user.institution_id
        payload["institution_id"] = institution_id

    if not payload.get("programme_id") or not payload.get("academic_year"):
        return _make_error_response("VALIDATION_ERROR", "programme_id and academic_year are required.", status_code=422)

    if not payload.get("full_name") or not payload.get("institution_student_id"):
        return _make_error_response("VALIDATION_ERROR", "full_name and institution_student_id are required.", status_code=422)

    try:
        res = seat_ledger.commit_admission(payload, actor=current_user)
        if "admission_event" in res and "event" not in res:
            res["event"] = res["admission_event"]
        return jsonify({
            "ok": True,
            "data": res
        }), 201

    except seat_ledger.CapacityExceeded as exc:
        return _make_error_response("CAPACITY_EXCEEDED", exc.message, details=exc.details, status_code=409)

    except seat_ledger.DuplicateAdmission as exc:
        return _make_error_response("DUPLICATE_ADMISSION", exc.message, details=exc.details, status_code=409)

    except seat_ledger.CategoryNotEligible as exc:
        return _make_error_response("CATEGORY_NOT_ELIGIBLE", exc.message, details=exc.details, status_code=409)

    except seat_ledger.CapacityNotFound as exc:
        return _make_error_response("NOT_FOUND", exc.message, details=exc.details, status_code=404)

    except seat_ledger.SeatLedgerError as exc:
        return _make_error_response(exc.reason_code, exc.message, details=exc.details, status_code=409)


@admissions_bp.route("/api/admissions/events", methods=["GET"])
@login_required
def get_admission_events():
    """
    Paginated admission event history, including both ACCEPTED and REJECTED attempts.
    Filters: result, institution_id, academic_year, programme_id, page, per_page.
    """
    if current_user.role not in (Role.AICTE_ADMIN.value, Role.INSTITUTION_ADMIN.value, Role.AUDITOR.value):
        return _make_error_response("FORBIDDEN", "Access denied.", status_code=403)

    page = max(1, request.args.get("page", 1, type=int))
    per_page = min(100, max(1, request.args.get("per_page", 25, type=int)))

    result_filter = request.args.get("result")
    reason_filter = request.args.get("reason_code")
    year_filter = request.args.get("academic_year")
    prog_filter = request.args.get("programme_id")
    inst_filter = request.args.get("institution_id")

    query = AdmissionEvent.query.join(Institution).join(Programme)

    # Scoping
    if current_user.role == Role.INSTITUTION_ADMIN.value:
        query = query.filter(AdmissionEvent.institution_id == current_user.institution_id)
    elif inst_filter:
        query = query.filter(AdmissionEvent.institution_id == inst_filter)

    if result_filter:
        query = query.filter(AdmissionEvent.result == result_filter.upper())
    if reason_filter:
        query = query.filter(AdmissionEvent.reason_code == reason_filter)
    if year_filter:
        query = query.filter(AdmissionEvent.academic_year == year_filter)
    if prog_filter:
        query = query.filter(AdmissionEvent.programme_id == prog_filter)

    total = query.count()
    events = (
        query.order_by(AdmissionEvent.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    items = []
    for ev in events:
        item = ev.to_dict()
        item["institution_code"] = ev.institution.code if ev.institution else None
        item["institution_name"] = ev.institution.name if ev.institution else None
        item["programme_code"] = ev.programme.code if ev.programme else None
        item["programme_name"] = ev.programme.name if ev.programme else None

        # Attach student identifiers if linked
        if ev.student_id:
            st = db.session.get(StudentRecord, ev.student_id)
            if st:
                item["student_name"] = st.full_name
                item["institution_student_id"] = st.institution_student_id
                item["apaar_ref_masked"] = mask_apaar(st.apaar_ref)
        items.append(item)

    total_pages = (total + per_page - 1) // per_page

    return jsonify({
        "ok": True,
        "data": items,
        "meta": {
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
        }
    }), 200
