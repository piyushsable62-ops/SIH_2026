"""
Dashboard routes for APAAR Nexus (Task A11).
Shared between Member A (institution view/metrics) and Member B (governance view).
Provides:
- GET /dashboard — Authenticated landing portal view.
- GET /dashboard/institution-preview — Temporary JSON viewer for institution metrics.
- GET /api/dashboard/institution — Dynamic institution metrics API (Member A).
"""
from typing import Any, Dict, Optional
from flask import Blueprint, render_template, request, jsonify, current_app
from flask_login import login_required, current_user

from extensions import db
from models.institution import Institution, Programme, IntakeCapacity
from models.student import StudentRecord
from models.admission import DataSubmission, SubmissionError
from models.enums import Role, EnrolmentStatus, Classification, AdmissionCategory, ValidationStatus
from services.authz import scope_query
from services.seat_ledger import calculate_warning_level

dashboards_bp = Blueprint("dashboards", __name__)


def _make_error(code: str, message: str, status_code: int = 400, details: Optional[dict] = None):
    """Standard API error response envelope."""
    return jsonify({
        "ok": False,
        "error": {
            "code": code,
            "message": message,
            "details": details or {}
        }
    }), status_code


@dashboards_bp.route("/dashboard", methods=["GET"])
@login_required
def dashboard_page():
    """Render the dashboard portal page for authenticated users."""
    role = current_user.role.value if hasattr(current_user.role, "value") else str(current_user.role)
    institution = None
    if current_user.institution_id:
        institution = db.session.get(Institution, current_user.institution_id)

    if role == Role.AICTE_ADMIN.value:
        stats = {
            "institutions_count": Institution.query.count(),
            "programmes_count": Programme.query.count(),
            "students_count": StudentRecord.query.count(),
            "active_enrolments": StudentRecord.query.filter_by(enrolment_status=EnrolmentStatus.ACTIVE.value).count(),
        }
    elif role == Role.INSTITUTION_ADMIN.value and institution:
        scoped = scope_query(StudentRecord.query, StudentRecord)
        stats = {
            "institution_name": institution.name,
            "institution_code": institution.code,
            "students_count": scoped.count(),
            "programmes_count": Programme.query.filter_by(institution_id=institution.id).count(),
            "active_enrolments": scoped.filter_by(enrolment_status=EnrolmentStatus.ACTIVE.value).count(),
        }
    else:
        stats = {
            "students_count": 0,
            "programmes_count": 0,
            "active_enrolments": 0,
        }

    return render_template(
        "dashboard.html",
        stats=stats,
        institution=institution,
    )


@dashboards_bp.route("/dashboard/institution-preview", methods=["GET"])
@login_required
def dashboard_institution_preview():
    """Temporary JSON viewer page for institution dashboard metrics while Member B finishes dashboard.js."""
    user_role = current_user.role.value if hasattr(current_user.role, "value") else str(current_user.role)
    if user_role not in (Role.AICTE_ADMIN.value, Role.INSTITUTION_ADMIN.value, Role.AUDITOR.value):
        return _make_error("FORBIDDEN", "Access denied.", 403)

    return render_template(
        "institution_dashboard_preview.html",
        user_role=user_role,
        institution_id=current_user.institution_id,
    )


# ==============================================================================
# Institution Dashboard Metrics API (Task A11)
# ==============================================================================

@dashboards_bp.route("/api/dashboard/institution", methods=["GET"])
@login_required
def get_institution_dashboard():
    """
    Comprehensive institution-level compliance, capacity, risk, and intake metrics.
    Scope:
      - INSTITUTION_ADMIN: Strictly scoped to own institution (403 on mismatch).
      - AICTE_ADMIN / AUDITOR: Can specify ?institution_id=; defaults to first active institution.
    Filters:
      - academic_year: Academic session filter (e.g., '2026-27').
      - programme_id: Specific degree programme UUID.
      - category: Specific admission category (OPEN, OBC, etc.).
      - validation_status: Student validation status (VALID, FLAGGED, etc.).
      - risk_level: Risk severity band (LOW, MEDIUM, HIGH, CRITICAL).
    """
    user_role = current_user.role.value if hasattr(current_user.role, "value") else str(current_user.role)
    if user_role not in (Role.INSTITUTION_ADMIN.value, Role.AICTE_ADMIN.value, Role.AUDITOR.value):
        return _make_error("FORBIDDEN", "Access denied.", 403)

    req_inst_id = request.args.get("institution_id")

    if user_role == Role.INSTITUTION_ADMIN.value:
        if req_inst_id and req_inst_id != current_user.institution_id:
            return _make_error("FORBIDDEN", "Cannot access metrics for another institution.", 403)
        target_inst_id = current_user.institution_id
    else:
        # AICTE_ADMIN or AUDITOR
        if req_inst_id:
            target_inst_id = req_inst_id
        else:
            first_inst = Institution.query.filter_by(status="ACTIVE").order_by(Institution.name.asc()).first()
            if not first_inst:
                first_inst = Institution.query.first()
            target_inst_id = first_inst.id if first_inst else None

    if not target_inst_id:
        return _make_error("NOT_FOUND", "No institution found.", 404)

    institution = db.session.get(Institution, target_inst_id)
    if not institution:
        return _make_error("NOT_FOUND", f"Institution '{target_inst_id}' was not found.", 404)

    # Extract query filters
    academic_year = request.args.get("academic_year") or "2026-27"
    programme_id = request.args.get("programme_id")
    category = request.args.get("category")
    validation_status = request.args.get("validation_status")
    risk_level = request.args.get("risk_level")

    # 1. Seat Capacity & Intake Calculation
    cap_query = IntakeCapacity.query.filter_by(institution_id=target_inst_id)
    if academic_year and academic_year.upper() != "ALL":
        cap_query = cap_query.filter_by(academic_year=academic_year)
    if programme_id:
        cap_query = cap_query.filter_by(programme_id=programme_id)

    capacities = cap_query.all()

    approved_regular = sum(c.approved_regular_intake for c in capacities)
    supernumerary_capacity = sum(c.supernumerary_capacity for c in capacities)

    # Query active/graduated students for admissions count
    active_statuses = [EnrolmentStatus.ACTIVE.value, EnrolmentStatus.GRADUATED.value]
    stud_query = StudentRecord.query.filter_by(institution_id=target_inst_id)
    if academic_year and academic_year.upper() != "ALL":
        stud_query = stud_query.filter_by(academic_year=academic_year)
    if programme_id:
        stud_query = stud_query.filter_by(programme_id=programme_id)
    if category:
        stud_query = stud_query.filter_by(admission_category=category.upper())
    if validation_status:
        stud_query = stud_query.filter_by(validation_status=validation_status.upper())

    regular_admitted = stud_query.filter(
        StudentRecord.classification == Classification.REGULAR.value,
        StudentRecord.enrolment_status.in_(active_statuses)
    ).count()

    supernumerary_admitted = stud_query.filter(
        StudentRecord.classification == Classification.SUPERNUMERARY.value,
        StudentRecord.enrolment_status.in_(active_statuses)
    ).count()

    regular_remaining = max(0, approved_regular - regular_admitted)
    supernumerary_remaining = max(0, supernumerary_capacity - supernumerary_admitted)

    regular_utilisation_pct = (
        round((regular_admitted / approved_regular * 100), 1) if approved_regular > 0 else 0.0
    )
    supernumerary_utilisation_pct = (
        round((supernumerary_admitted / supernumerary_capacity * 100), 1)
        if supernumerary_capacity > 0
        else 0.0
    )

    warning_level = calculate_warning_level(regular_utilisation_pct)

    # Category-wise capacity and admitted breakdown
    standard_categories = ["OPEN", "OBC", "SC", "ST", "EWS", "NRI", "OCI", "FN"]
    cat_query = (
        db.session.query(StudentRecord.admission_category, db.func.count(StudentRecord.id))
        .filter(
            StudentRecord.institution_id == target_inst_id,
            StudentRecord.enrolment_status.in_(active_statuses)
        )
    )
    if academic_year and academic_year.upper() != "ALL":
        cat_query = cat_query.filter(StudentRecord.academic_year == academic_year)
    if programme_id:
        cat_query = cat_query.filter(StudentRecord.programme_id == programme_id)
    if category:
        cat_query = cat_query.filter(StudentRecord.admission_category == category.upper())
    if validation_status:
        cat_query = cat_query.filter(StudentRecord.validation_status == validation_status.upper())

    cat_counts = dict(cat_query.group_by(StudentRecord.admission_category).all())

    category_wise_capacity = {}
    for c_code in standard_categories:
        c_admitted = cat_counts.get(c_code, 0)
        c_cap_limit = None
        for cap in capacities:
            if cap.category_rules and c_code in cap.category_rules:
                rule_val = cap.category_rules[c_code]
                if isinstance(rule_val, (int, float)):
                    c_cap_limit = (c_cap_limit or 0) + int(rule_val)

        c_util_pct = round((c_admitted / c_cap_limit * 100), 1) if c_cap_limit and c_cap_limit > 0 else None
        category_wise_capacity[c_code] = {
            "category": c_code,
            "admitted": c_admitted,
            "capacity": c_cap_limit,
            "utilisation_pct": c_util_pct
        }

    # 2. Submission History (last 10)
    submissions = (
        DataSubmission.query.filter_by(institution_id=target_inst_id)
        .order_by(DataSubmission.created_at.desc())
        .limit(10)
        .all()
    )
    submission_history = [s.to_dict() for s in submissions]

    # 3. Validation Error Counts by Code
    error_counts_query = (
        db.session.query(SubmissionError.code, db.func.count(SubmissionError.id))
        .join(DataSubmission, SubmissionError.submission_id == DataSubmission.id)
        .filter(DataSubmission.institution_id == target_inst_id)
        .group_by(SubmissionError.code)
        .all()
    )
    validation_error_counts = {code: count for code, count in error_counts_query}

    # 4. Pending Identity Checks (validation_status PENDING/FLAGGED)
    base_check_query = StudentRecord.query.filter_by(institution_id=target_inst_id)
    if academic_year and academic_year.upper() != "ALL":
        base_check_query = base_check_query.filter_by(academic_year=academic_year)
    if programme_id:
        base_check_query = base_check_query.filter_by(programme_id=programme_id)

    pending_count = base_check_query.filter_by(validation_status=ValidationStatus.PENDING.value).count()
    flagged_count = base_check_query.filter_by(validation_status=ValidationStatus.FLAGGED.value).count()

    pending_identity_checks = {
        "pending": pending_count,
        "flagged": flagged_count,
        "total": pending_count + flagged_count
    }

    # 5. Institutional Risk Cases (count by severity; tolerate empty table)
    risk_cases_by_severity = {"LOW": 0, "MEDIUM": 0, "HIGH": 0, "CRITICAL": 0, "total": 0}
    try:
        from models.risk import RiskCase
        rc_query = db.session.query(RiskCase.severity, db.func.count(RiskCase.id)).filter(
            RiskCase.institution_id == target_inst_id
        )
        if risk_level:
            rc_query = rc_query.filter(RiskCase.severity == risk_level.strip().upper())
        for sev, count in rc_query.group_by(RiskCase.severity).all():
            if sev in risk_cases_by_severity:
                risk_cases_by_severity[sev] = count
        risk_cases_by_severity["total"] = sum(v for k, v in risk_cases_by_severity.items() if k != "total")
    except Exception as exc:
        current_app.logger.warning("Could not query risk cases for institution %s: %s", target_inst_id, exc)

    # 6. Credential Issuance History (last 10; tolerate empty table)
    credential_issuance_history = []
    try:
        from models.credential import Credential
        creds = (
            Credential.query.filter_by(institution_id=target_inst_id)
            .order_by(Credential.created_at.desc())
            .limit(10)
            .all()
        )
        for c in creds:
            st = db.session.get(StudentRecord, c.student_id) if c.student_id else None
            credential_issuance_history.append({
                "id": c.id,
                "reference": c.reference,
                "student_id": c.student_id,
                "student_name": st.full_name if st else "Unknown",
                "programme_name": c.programme_name,
                "award_year": c.award_year,
                "status": c.status_stored,
                "issue_date": c.issue_date.isoformat() if hasattr(c, "issue_date") and c.issue_date else None,
                "created_at": c.created_at.isoformat() if c.created_at else None,
            })
    except Exception as exc:
        current_app.logger.warning("Could not query credentials for institution %s: %s", target_inst_id, exc)

    payload = {
        "institution": {
            "id": institution.id,
            "name": institution.name,
            "code": institution.code,
            "status": institution.status,
        },
        "filters_applied": {
            "academic_year": academic_year,
            "programme_id": programme_id,
            "category": category,
            "validation_status": validation_status,
            "risk_level": risk_level,
        },
        "seat_summary": {
            "approved_regular_intake": approved_regular,
            "regular_admitted": regular_admitted,
            "regular_remaining": regular_remaining,
            "regular_utilisation_pct": regular_utilisation_pct,
            "supernumerary_capacity": supernumerary_capacity,
            "supernumerary_admitted": supernumerary_admitted,
            "supernumerary_remaining": supernumerary_remaining,
            "supernumerary_utilisation_pct": supernumerary_utilisation_pct,
            "total_approved_intake": approved_regular + supernumerary_capacity,
            "total_admitted": regular_admitted + supernumerary_admitted,
            "warning_level": warning_level,
            "category_capacity": category_wise_capacity,
        },
        "submission_history": submission_history,
        "validation_error_counts": validation_error_counts,
        "pending_identity_checks": pending_identity_checks,
        "risk_cases_by_severity": risk_cases_by_severity,
        "credential_issuance_history": credential_issuance_history,
    }

    return jsonify({
        "ok": True,
        "data": payload
    }), 200
