"""
Dashboard routes for APAAR Nexus.
Shared between Member A (institution view/metrics) and Member B (governance view).
Provides authenticated landing portal view at /dashboard.
"""
from flask import Blueprint, render_template
from flask_login import login_required, current_user

from extensions import db
from models.institution import Institution, Programme
from models.student import StudentRecord
from models.enums import Role, EnrolmentStatus
from services.authz import scope_query

dashboards_bp = Blueprint("dashboards", __name__)


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
