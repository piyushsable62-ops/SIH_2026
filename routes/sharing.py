"""Consent APIs and rate-limited public, minimal-disclosure verification."""
import logging
import re

from flask import Blueprint, jsonify, render_template, request
from flask_login import current_user, login_required
from sqlalchemy import select

from extensions import db
from models import SharingSession, StudentRecord
from routes.credentials import domain_error, public_url, qr_uri
from routes.risk import integer, json_body
from services import sharing_service as service
from services.authz import roles_required, scope_query
from services.credential_service import public_limit

sharing_bp = Blueprint("sharing", __name__)


class RedactSharingPath(logging.Filter):
    """Werkzeug access logs must never retain a bearer token from a request URL."""
    def filter(self, record):
        record.msg = re.sub(r"/public/verify/[^\s?\"<>]+", "/public/verify/[REDACTED]", record.getMessage())
        record.args = ()
        return True


@sharing_bp.record_once
def redact_access_logs(state):
    """Install once per process; production proxies must also redact this path."""
    logger = logging.getLogger("werkzeug")
    if not any(isinstance(item, RedactSharingPath) for item in logger.filters):
        logger.addFilter(RedactSharingPath())


@sharing_bp.errorhandler(ValueError)
def invalid_input(error):
    """Use the standard safe validation envelope."""
    return domain_error(service.Error("Invalid request data."))


@sharing_bp.after_request
def private_headers(response):
    """Keep bearer links out of caches, referrers and search indexes."""
    response.headers.update({"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex", "X-Content-Type-Options": "nosniff"})
    return response


@sharing_bp.post("/api/sharing-sessions")
@login_required
@roles_required("STUDENT")
def create():
    """Return the full secret only in this creation response."""
    body = json_body({"credential_id", "claims", "expiry_hours", "explicit_authorisation"})
    student = db.session.get(StudentRecord, current_user.student_id) if current_user.student_id else None
    row, token = service.create_sharing_session(student, body.get("credential_id"), body.get("claims"), body.get("expiry_hours"),
        current_user, explicit_authorisation=body.get("explicit_authorisation"))
    url = public_url("/public/verify/" + token)
    return jsonify(ok=True, data={"id": row.id, "token": token, "share_url": url, "qr_data_uri": qr_uri(url),
        "expires_at": service.credentials.iso(row.expires_at), "claims": row.claims}), 201


@sharing_bp.get("/api/sharing-sessions")
@login_required
@roles_required("STUDENT", "AICTE_ADMIN", "AUDITOR")
def listing():
    """Student-own or oversight metadata only; never return a token/hash."""
    page, size = integer(request.args.get("page"), "page", 1, 1000000), integer(request.args.get("per_page"), "per_page", 25, 100)
    query = scope_query(select(SharingSession), SharingSession).order_by(SharingSession.created_at.desc(), SharingSession.id)
    rows = db.paginate(query, page=page, per_page=size, error_out=False)
    return jsonify(ok=True, data=[service.metadata(row) for row in rows.items], meta={"page": page, "per_page": size, "total": rows.total})


@sharing_bp.post("/api/sharing-sessions/<id_>/revoke")
@login_required
@roles_required("STUDENT", "AICTE_ADMIN")
def revoke(id_):
    """Support revocations are explicit administrator actions and audit-logged."""
    json_body(set())
    return jsonify(ok=True, data=service.metadata(service.revoke_session(id_, current_user)))


@sharing_bp.get("/public/verify", defaults={"token": ""})
@sharing_bp.get("/public/verify/", defaults={"token": ""})
@sharing_bp.get("/public/verify/<path:token>")
def public_verify(token):
    """Public GET discloses only selected fields; unknown secrets share one 404."""
    public_limit("sharing")
    status = 200
    try:
        outcome = service.resolve_token(token)
    except service.Error as error:
        if error.status != 404 or request.args.get("format") == "json":
            raise
        status = 404
        outcome = {"result": "INVALID", "message": service.INVALID_MESSAGE, "claims": {}, "expires_at": None}
    if request.args.get("format") == "json":
        return jsonify(ok=True, data=outcome)
    return render_template("public_verify.html", outcome=outcome,
        recorded=current_user.is_authenticated and current_user.role == "VERIFIER"), status


@sharing_bp.get("/sharing")
@login_required
@roles_required("STUDENT")
def sharing_page():
    """Render controls only; never pre-embed secrets or profile data in the HTML."""
    if not current_user.student_id or db.session.get(StudentRecord, current_user.student_id) is None:
        raise service.Error("Student profile not found.", "NOT_FOUND", 404)
    return render_template("sharing.html", expiry_hours=service.allowed_expiry_hours())
