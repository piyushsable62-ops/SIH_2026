"""
Reporting routes and CSV exports for APAAR Nexus (Task A11).
Smart India Hackathon Problem Statement PS 26263.

Provides:
- GET /api/reports/enrolment — Enrolment compliance and seat utilisation report (JSON).
- GET /api/reports/enrolment.csv — Clean CSV download with formula-injection neutralization.
- GET /api/audit-logs — Paginated, filtered, scope-aware audit log query API.
- GET /audit-logs — HTML view for immutable system audit logs.
- GET /reports — HTML view for enrolment and governance reports.
"""
import io
import csv
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from flask import Blueprint, request, jsonify, render_template, Response, current_app
from flask_login import login_required, current_user

from extensions import db
from models.institution import Institution, Programme, IntakeCapacity
from models.student import StudentRecord
from models.enums import Role, Classification, AdmissionCategory, EnrolmentStatus
from services import seat_ledger
from services import audit_service
from services.audit_service import log as audit_log

reports_bp = Blueprint("reports", __name__)


def _sanitize_csv_cell(value: Any) -> str:
    """
    Sanitize CSV field to neutralize spreadsheet formula injection vulnerabilities.
    Prefixes values starting with '=', '+', '-', '@', '\\t', or '\\r' with a single quote.
    """
    if value is None:
        return ""
    text = str(value).strip()
    if text and text[0] in ("=", "+", "-", "@", "\t", "\r"):
        return f"'{text}"
    return text


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


# ==============================================================================
# HTML Page Views
# ==============================================================================

@reports_bp.route("/audit-logs", methods=["GET"])
@login_required
def audit_logs_page():
    """Render the immutable audit logs page (AICTE_ADMIN, AUDITOR, INSTITUTION_ADMIN)."""
    user_role = current_user.role.value if hasattr(current_user.role, "value") else str(current_user.role)
    if user_role not in (Role.AICTE_ADMIN.value, Role.AUDITOR.value, Role.INSTITUTION_ADMIN.value):
        return _make_error("FORBIDDEN", "Access denied.", 403)

    is_global = (user_role in (Role.AICTE_ADMIN.value, Role.AUDITOR.value))
    institutions = []
    if is_global:
        institutions = Institution.query.filter_by(status="ACTIVE").order_by(Institution.name.asc()).all()
    elif current_user.institution_id:
        inst = db.session.get(Institution, current_user.institution_id)
        if inst:
            institutions = [inst]

    return render_template(
        "audit_logs.html",
        is_global=is_global,
        user_institution_id=current_user.institution_id if not is_global else None,
        institutions=institutions,
    )


@reports_bp.route("/reports", methods=["GET"])
@login_required
def reports_page():
    """Render the official reports portal page."""
    user_role = current_user.role.value if hasattr(current_user.role, "value") else str(current_user.role)
    if user_role not in (Role.AICTE_ADMIN.value, Role.INSTITUTION_ADMIN.value, Role.AUDITOR.value):
        return _make_error("FORBIDDEN", "Access denied.", 403)

    is_global = (user_role in (Role.AICTE_ADMIN.value, Role.AUDITOR.value))
    institutions = []
    if is_global:
        institutions = Institution.query.filter_by(status="ACTIVE").order_by(Institution.name.asc()).all()
    elif current_user.institution_id:
        inst = db.session.get(Institution, current_user.institution_id)
        if inst:
            institutions = [inst]

    return render_template(
        "reports.html",
        is_global=is_global,
        user_institution_id=current_user.institution_id if not is_global else None,
        institutions=institutions,
        academic_years=["2026-27", "2025-26", "2024-25"],
    )


# ==============================================================================
# Audit Logs API
# ==============================================================================

@reports_bp.route("/api/audit-logs", methods=["GET"])
@login_required
def list_audit_logs():
    """
    Query append-only audit logs with filters and pagination.
    Scope:
      - AICTE_ADMIN and AUDITOR see all institutions (can filter by institution_id).
      - INSTITUTION_ADMIN strictly restricted to own institution.
    """
    user_role = current_user.role.value if hasattr(current_user.role, "value") else str(current_user.role)
    if user_role not in (Role.AICTE_ADMIN.value, Role.AUDITOR.value, Role.INSTITUTION_ADMIN.value):
        return _make_error("FORBIDDEN", "Access denied.", 403)

    req_inst_id = request.args.get("institution_id")
    if user_role == Role.INSTITUTION_ADMIN.value:
        inst_id = current_user.institution_id
        if req_inst_id and req_inst_id != current_user.institution_id:
            return _make_error("FORBIDDEN", "Cannot view audit logs of other institutions.", 403)
    else:
        inst_id = req_inst_id if req_inst_id else None

    actor = request.args.get("actor")
    action = request.args.get("action")
    entity_type = request.args.get("entity_type")
    date_from = request.args.get("date_from")
    date_to = request.args.get("date_to")

    try:
        page = max(1, int(request.args.get("page", 1)))
        per_page = min(100, max(1, int(request.args.get("per_page", 25))))
    except (ValueError, TypeError):
        page = 1
        per_page = 25

    items, total = audit_service.query_audit_logs(
        actor=actor,
        action=action,
        entity_type=entity_type,
        institution_id=inst_id,
        date_from=date_from,
        date_to=date_to,
        page=page,
        per_page=per_page,
    )

    return jsonify({
        "ok": True,
        "data": items,
        "meta": {
            "page": page,
            "per_page": per_page,
            "total": total
        }
    }), 200


# ==============================================================================
# Enrolment Report (JSON & Clean CSV)
# ==============================================================================

def _build_enrolment_data(institution_id: Optional[str], academic_year: str, programme_id: Optional[str] = None):
    """
    Compile enrolment report rows for matching institutions and programmes.
    Returns (rows, summary_totals).
    """
    query = (
        IntakeCapacity.query.join(Programme, IntakeCapacity.programme_id == Programme.id)
        .join(Institution, IntakeCapacity.institution_id == Institution.id)
    )

    if institution_id:
        query = query.filter(IntakeCapacity.institution_id == institution_id)
    if academic_year:
        query = query.filter(IntakeCapacity.academic_year == academic_year)
    if programme_id:
        query = query.filter(IntakeCapacity.programme_id == programme_id)

    capacities = query.order_by(Institution.name.asc(), Programme.name.asc()).all()

    standard_categories = ["OPEN", "OBC", "SC", "ST", "EWS", "NRI", "OCI", "FN"]
    rows = []
    totals = {
        "approved_regular": 0,
        "regular_admitted": 0,
        "regular_remaining": 0,
        "supernumerary_capacity": 0,
        "supernumerary_admitted": 0,
        "supernumerary_remaining": 0,
        "total_approved": 0,
        "total_admitted": 0,
        "category_counts": {c: 0 for c in standard_categories},
    }

    active_statuses = [EnrolmentStatus.ACTIVE.value, EnrolmentStatus.GRADUATED.value]

    for cap in capacities:
        try:
            summary = seat_ledger.get_summary(cap.institution_id, cap.programme_id, cap.academic_year)
        except seat_ledger.CapacityNotFound:
            continue

        cat_breakdown = summary.get("category_breakdown", {})
        cat_counts = {c: cat_breakdown.get(c, 0) for c in standard_categories}

        row = {
            "institution_id": cap.institution_id,
            "institution_code": cap.institution.code,
            "institution_name": cap.institution.name,
            "programme_id": cap.programme_id,
            "programme_code": cap.programme.code,
            "programme_name": cap.programme.name,
            "programme_level": cap.programme.level,
            "academic_year": cap.academic_year,
            "approved_regular_intake": summary["approved_regular"],
            "regular_admitted": summary["regular_admitted"],
            "regular_remaining": summary["regular_remaining"],
            "regular_utilisation_pct": summary["utilisation_pct"],
            "supernumerary_capacity": summary["supernumerary_capacity"],
            "supernumerary_admitted": summary["supernumerary_admitted"],
            "supernumerary_remaining": summary["supernumerary_remaining"],
            "supernumerary_utilisation_pct": summary["supernumerary_utilisation_pct"],
            "total_approved_intake": summary["approved_regular"] + summary["supernumerary_capacity"],
            "total_admitted": summary["regular_admitted"] + summary["supernumerary_admitted"],
            "warning_level": summary["warning_level"],
            "category_counts": cat_counts,
        }
        rows.append(row)

        totals["approved_regular"] += row["approved_regular_intake"]
        totals["regular_admitted"] += row["regular_admitted"]
        totals["regular_remaining"] += row["regular_remaining"]
        totals["supernumerary_capacity"] += row["supernumerary_capacity"]
        totals["supernumerary_admitted"] += row["supernumerary_admitted"]
        totals["supernumerary_remaining"] += row["supernumerary_remaining"]
        totals["total_approved"] += row["total_approved_intake"]
        totals["total_admitted"] += row["total_admitted"]
        for c in standard_categories:
            totals["category_counts"][c] += cat_counts[c]

    totals["regular_utilisation_pct"] = (
        round((totals["regular_admitted"] / totals["approved_regular"] * 100), 1)
        if totals["approved_regular"] > 0
        else 0.0
    )
    totals["supernumerary_utilisation_pct"] = (
        round((totals["supernumerary_admitted"] / totals["supernumerary_capacity"] * 100), 1)
        if totals["supernumerary_capacity"] > 0
        else 0.0
    )

    return rows, totals


@reports_bp.route("/api/reports/enrolment", methods=["GET"])
@login_required
def get_enrolment_report():
    """
    Enrolment compliance and seat utilisation report (JSON).
    Query params: academic_year, institution_id, programme_id.
    Scope: INSTITUTION_ADMIN sees only own institution; AICTE_ADMIN/AUDITOR see all or filtered.
    """
    user_role = current_user.role.value if hasattr(current_user.role, "value") else str(current_user.role)
    if user_role not in (Role.AICTE_ADMIN.value, Role.INSTITUTION_ADMIN.value, Role.AUDITOR.value):
        return _make_error("FORBIDDEN", "Access denied.", 403)

    req_inst_id = request.args.get("institution_id")
    if user_role == Role.INSTITUTION_ADMIN.value:
        if req_inst_id and req_inst_id != current_user.institution_id:
            return _make_error("FORBIDDEN", "Cannot view enrolment report for other institutions.", 403)
        inst_id = current_user.institution_id
    else:
        inst_id = req_inst_id if req_inst_id else None

    academic_year = request.args.get("academic_year") or "2026-27"
    programme_id = request.args.get("programme_id")

    rows, totals = _build_enrolment_data(inst_id, academic_year, programme_id)

    return jsonify({
        "ok": True,
        "data": {
            "academic_year": academic_year,
            "institution_id": inst_id,
            "rows": rows,
            "summary_totals": totals
        },
        "meta": {
            "total_programmes": len(rows),
            "academic_year": academic_year
        }
    }), 200


@reports_bp.route("/api/reports/enrolment.csv", methods=["GET"])
@login_required
def download_enrolment_report_csv():
    """
    Clean CSV download of enrolment compliance report.
    - No comment rows or extra headers.
    - Formula injection neutralized.
    - Filename strictly contains 'SYNTHETIC' (e.g. enrolment_report_SYNTHETIC_2026-27.csv).
    - Mutating export action is audit-logged.
    """
    user_role = current_user.role.value if hasattr(current_user.role, "value") else str(current_user.role)
    if user_role not in (Role.AICTE_ADMIN.value, Role.INSTITUTION_ADMIN.value, Role.AUDITOR.value):
        return _make_error("FORBIDDEN", "Access denied.", 403)

    req_inst_id = request.args.get("institution_id")
    if user_role == Role.INSTITUTION_ADMIN.value:
        if req_inst_id and req_inst_id != current_user.institution_id:
            return _make_error("FORBIDDEN", "Cannot download enrolment report for other institutions.", 403)
        inst_id = current_user.institution_id
    else:
        inst_id = req_inst_id if req_inst_id else None

    academic_year = request.args.get("academic_year") or "2026-27"
    programme_id = request.args.get("programme_id")

    rows, _ = _build_enrolment_data(inst_id, academic_year, programme_id)

    standard_categories = ["OPEN", "OBC", "SC", "ST", "EWS", "NRI", "OCI", "FN"]

    headers = [
        "Institution Code",
        "Institution Name",
        "Programme Code",
        "Programme Name",
        "Programme Level",
        "Academic Year",
        "Approved Regular Intake",
        "Regular Admitted",
        "Regular Remaining",
        "Regular Utilisation %",
        "Supernumerary Capacity",
        "Supernumerary Admitted",
        "Supernumerary Remaining",
        "Supernumerary Utilisation %",
        "Total Approved Intake",
        "Total Admitted",
    ] + standard_categories

    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(headers)

    for r in rows:
        csv_row = [
            _sanitize_csv_cell(r["institution_code"]),
            _sanitize_csv_cell(r["institution_name"]),
            _sanitize_csv_cell(r["programme_code"]),
            _sanitize_csv_cell(r["programme_name"]),
            _sanitize_csv_cell(r["programme_level"]),
            _sanitize_csv_cell(r["academic_year"]),
            r["approved_regular_intake"],
            r["regular_admitted"],
            r["regular_remaining"],
            r["regular_utilisation_pct"],
            r["supernumerary_capacity"],
            r["supernumerary_admitted"],
            r["supernumerary_remaining"],
            r["supernumerary_utilisation_pct"],
            r["total_approved_intake"],
            r["total_admitted"],
        ] + [r["category_counts"].get(c, 0) for c in standard_categories]
        writer.writerow(csv_row)

    csv_content = output.getvalue()
    output.close()

    # Filename format per contract: must include "SYNTHETIC"
    clean_year = re.sub(r"[^a-zA-Z0-9_-]", "", academic_year)
    if inst_id:
        inst_obj = db.session.get(Institution, inst_id)
        code_tag = f"_{inst_obj.code}" if inst_obj else ""
        filename = f"enrolment_report_SYNTHETIC{code_tag}_{clean_year}.csv"
    else:
        filename = f"enrolment_report_SYNTHETIC_ALL_{clean_year}.csv"

    # Audit log the CSV export action
    audit_log(
        actor=current_user.username,
        action="EXPORT_ENROLMENT_REPORT_CSV",
        entity_type="REPORT",
        entity_id=f"ENROLMENT_{clean_year}",
        details={
            "filename": filename,
            "academic_year": academic_year,
            "institution_id": inst_id,
            "records_count": len(rows)
        },
        institution_id=inst_id
    )

    return Response(
        csv_content,
        mimetype="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename={filename}",
            "Content-Type": "text/csv; charset=utf-8"
        }
    )
