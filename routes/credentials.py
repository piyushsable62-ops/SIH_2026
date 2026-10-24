"""Scoped credential issuance, passport and QR APIs; private keys never leave services."""
import base64
from io import BytesIO
from urllib.parse import quote, urlsplit

from flask import Blueprint, current_app, jsonify, render_template, request, Response, redirect, url_for
from flask_login import current_user, login_required
import qrcode
from sqlalchemy import func, select

from extensions import db
from models import Credential, Institution, StudentRecord, VerificationEvent
from routes.risk import integer, json_body
from services import credential_service as service
from services.authz import roles_required, scope_query
from services.risk_engine import public_record

credentials_bp = Blueprint("credentials", __name__)
READ_ROLES = ("AICTE_ADMIN", "INSTITUTION_ADMIN", "STUDENT", "AUDITOR")


@credentials_bp.app_errorhandler(service.CredentialError)
def domain_error(error):
    """Expose safe domain errors and explicit warning acknowledgements."""
    response = jsonify(ok=False, error={"code": error.code, "message": str(error), "details": error.details})
    response.status_code = error.status
    response.headers["Cache-Control"] = "no-store"
    if error.status == 429:
        response.headers["Retry-After"] = "60"
    return response


@credentials_bp.errorhandler(ValueError)
def input_error(error):
    """Reject malformed JSON/pagination without exposing internal parser details."""
    return domain_error(service.CredentialError("Invalid request data."))


def public_url(path: str) -> str:
    """Use a configured canonical origin when deployed; otherwise the request origin."""
    base = current_app.config.get("PUBLIC_BASE_URL") or request.host_url
    parsed = urlsplit(base)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise service.CredentialError("PUBLIC_BASE_URL must be an absolute HTTP(S) base URL.")
    return base.rstrip("/") + "/" + path.lstrip("/")


def qr_png(url: str) -> bytes:
    """Generate actual QR PNG bytes on the server; no external service or JS library."""
    image = qrcode.make(url)
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def qr_uri(url: str) -> str:
    """Encode a local QR image for authorized screens."""
    return "data:image/png;base64," + base64.b64encode(qr_png(url)).decode("ascii")


def scoped_credential(id_: str) -> Credential:
    """Unknown and out-of-scope credential IDs share the same 404 response."""
    row = db.session.execute(scope_query(select(Credential), Credential).where(Credential.id == id_)).scalar_one_or_none()
    if row is None:
        raise service.CredentialError("Credential not found in your scope.", "NOT_FOUND", 404)
    return row


def summary(row: Credential) -> dict:
    """Authenticated metadata only; payload bytes, signatures and private keys stay internal."""
    outcome = service.check_credential(row)
    return {**row.to_dict(), "result": outcome.result, "apaar_masked": service.mask_apaar(row.student.apaar_ref), "verification_url": public_url("/verify/" + quote(row.reference, safe="")),
            "institution_name": row.institution.name}


@credentials_bp.post("/api/credentials/issue")
@login_required
@roles_required("AICTE_ADMIN", "INSTITUTION_ADMIN")
def issue():
    """Issue after explicit warning acknowledgement, never as an automatic risk decision."""
    body = json_body({"student_id", "award_year", "valid_years", "acknowledge_warnings"})
    if not isinstance(body.get("student_id"), str) or "award_year" not in body:
        raise service.CredentialError("student_id and award_year are required.")
    row = service.issue_credential(body["student_id"], body["award_year"], valid_years=body.get("valid_years"),
        actor=current_user, acknowledge_warnings=body.get("acknowledge_warnings", False))
    return jsonify(ok=True, data={**summary(row), "warnings": row.warnings}), 201


@credentials_bp.get("/api/credentials")
@login_required
@roles_required(*READ_ROLES)
def list_credentials():
    """List only authorized credentials, with live computed status before pagination."""
    query = scope_query(select(Credential), Credential)
    for names, column in [(('institution', 'institution_id'), Credential.institution_id),
                          (('student', 'student_id'), Credential.student_id), (('year', 'award_year'), Credential.award_year)]:
        value = next((request.args[name] for name in names if name in request.args), None)
        if value:
            if column is Credential.institution_id and current_user.role == "INSTITUTION_ADMIN" and value != current_user.institution_id:
                raise service.CredentialError("Only your institution can be accessed.", "FORBIDDEN", 403)
            query = query.where(column == value)
    selected_status = request.args.get("status")
    if selected_status and selected_status not in {"ISSUED", "VERIFIED", "REVOKED", "EXPIRED", "INVALID"}:
        raise service.CredentialError("Invalid credential status.")
    rows = [summary(row) for row in db.session.execute(query.order_by(Credential.created_at.desc(), Credential.id)).scalars()]
    if selected_status:
        rows = [row for row in rows if (row["status_stored"] if selected_status == "ISSUED" else row["result"]) == selected_status]
    page = integer(request.args.get("page"), "page", 1, 1000000)
    per_page = integer(request.args.get("per_page"), "per_page", 25, 100)
    return jsonify(ok=True, data=rows[(page - 1) * per_page:page * per_page], meta={"page": page, "per_page": per_page, "total": len(rows)})


@credentials_bp.get("/api/credentials/<id_>")
@login_required
@roles_required(*READ_ROLES)
def detail(id_):
    """Return computed status, safe QR and event count for an authorized credential."""
    row = scoped_credential(id_)
    outcome = service.verify_credential(row.reference)
    data = summary(row)
    data.update(verification=outcome.to_dict(), verification_event_count=db.session.scalar(select(func.count()).select_from(VerificationEvent).where(VerificationEvent.credential_id == row.id)),
        qr_data_uri=qr_uri(data["verification_url"]))
    return jsonify(ok=True, data=data)


@credentials_bp.post("/api/credentials/<id_>/revoke")
@login_required
@roles_required("AICTE_ADMIN", "INSTITUTION_ADMIN")
def revoke(id_):
    """Require an authorized human's reason; no automatic revocation exists."""
    body = json_body({"reason"})
    return jsonify(ok=True, data=summary(service.revoke_credential(id_, body.get("reason"), current_user)))


@credentials_bp.get("/api/credentials/<id_>/qr.png")
@login_required
@roles_required(*READ_ROLES)
def qr(id_):
    """Serve a scoped QR PNG and never render another institution's credential."""
    row = scoped_credential(id_)
    return Response(qr_png(public_url("/verify/" + quote(row.reference, safe=""))), mimetype="image/png",
                    headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@credentials_bp.get("/api/credentials/eligible-students")
@login_required
@roles_required("AICTE_ADMIN", "INSTITUTION_ADMIN")
def eligible():
    """Search scoped eligible synthetic records before pagination, always masked."""
    from services.identity_resolution import normalize_name
    query = scope_query(select(StudentRecord), StudentRecord).where(StudentRecord.is_synthetic == True,
        StudentRecord.validation_status == "VALID", StudentRecord.enrolment_status.in_(["ACTIVE", "GRADUATED"]))
    text = request.args.get("q", "").strip()
    if len(text) > 150:
        raise service.CredentialError("Search is limited to 150 characters.")
    if text:
        query = query.where(StudentRecord.normalized_name.contains(normalize_name(text), autoescape=True))
    page = integer(request.args.get("page"), "page", 1, 1000000)
    total = db.session.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.session.execute(query.order_by(StudentRecord.full_name, StudentRecord.id).offset((page - 1) * 25).limit(25)).scalars()
    return jsonify(ok=True, data=[public_record(row) for row in rows], meta={"page": page, "per_page": 25, "total": total})


@credentials_bp.get("/api/passport/me")
@login_required
@roles_required("STUDENT")
def my_passport():
    """Own profile/credentials only; omit demographic/contact fields and full identifiers."""
    row = db.session.execute(scope_query(select(StudentRecord), StudentRecord)).scalar_one_or_none()
    if row is None:
        raise service.CredentialError("Student profile not found.", "NOT_FOUND", 404)
    profile = {key: value for key, value in public_record(row).items() if key not in {"id", "institution_id", "programme_id"}}
    profile["institution"] = row.institution.name
    rows = db.session.execute(scope_query(select(Credential), Credential).order_by(Credential.created_at.desc())).scalars()
    return jsonify(ok=True, data={"profile": profile, "credentials": [summary(item) for item in rows]})


@credentials_bp.get("/credentials")
@credentials_bp.get("/credentials/issue")
@login_required
@roles_required("AICTE_ADMIN", "INSTITUTION_ADMIN")
def issue_page():
    """Render real search, issue and revoke controls for official issuers."""
    return render_template("issue_credential.html", now_year=service.utcnow().year)


@credentials_bp.get("/passport")
@login_required
@roles_required("STUDENT")
def passport_page():
    """Render the authenticated student's academic passport."""
    return render_template("passport.html", credential_id=None)


@credentials_bp.get("/credentials/<id_>")
@login_required
@roles_required("AICTE_ADMIN", "INSTITUTION_ADMIN", "STUDENT", "AUDITOR")
def credential_page(id_):
    """Read-only in-scope passport, with no issue/revoke controls."""
    row = scoped_credential(id_)
    return render_template("passport.html", credential_id=row.id)


@credentials_bp.get("/verify/<reference>")
@credentials_bp.get("/verify/<reference>.json", endpoint="public_verify_json")
def public_verify(reference):
    """Public backend verdict only. All four outcomes, including unknown, use HTTP 200."""
    service.public_limit("credential")
    outcome = service.verify_credential(reference)
    if request.path.endswith(".json"):
        return jsonify(ok=True, data=outcome.to_dict())
    return render_template("verification.html", console=False, outcome=outcome, checked_at=service.utcnow().isoformat())


@credentials_bp.get("/verify")
@credentials_bp.get("/verify-console")
@login_required
def verify_console():
    """All authenticated roles can paste a reference or a trusted platform QR URL."""
    text = request.args.get("reference", "").strip()
    if text:
        import re
        parsed = urlsplit(text)
        if parsed.scheme or text.startswith("/verify/"):
            trusted = {urlsplit(request.host_url).netloc, urlsplit(current_app.config.get("PUBLIC_BASE_URL") or request.host_url).netloc}
            if parsed.scheme and (parsed.scheme not in {"http", "https"} or parsed.netloc not in trusted):
                raise service.CredentialError("Use a credential reference or this platform's QR link.")
            text = parsed.path.rsplit("/", 1)[-1]
        if not re.fullmatch(r"CRED-[A-Za-z0-9-]{1,24}", text):
            raise service.CredentialError("Enter a valid credential reference.")
        return redirect(url_for("credentials.public_verify", reference=text))
    demo_refs = ("CRED-DEMO01", "CRED-DEMO02", "CRED-DEMO03", "CRED-DEMO04")
    demos = db.session.execute(select(Credential).where(Credential.reference.in_(demo_refs)).order_by(Credential.reference)).scalars().all() if current_app.config.get("SHOW_DEMO_ACCOUNTS") else []
    recent = db.session.execute(select(VerificationEvent).where(VerificationEvent.verifier_user_id == current_user.id)
        .order_by(VerificationEvent.created_at.desc()).limit(10)).scalars().all() if current_user.role == "VERIFIER" else []
    return render_template("verification.html", console=True, demos=[{"reference": row.reference, "result": service.check_credential(row).result} for row in demos], recent=recent)


@credentials_bp.after_request
def private_headers(response):
    """Credential results and QR images are never cached by intermediaries."""
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response
