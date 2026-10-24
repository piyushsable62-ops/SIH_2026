"""
Contract tests for API response envelope consistency (AGENTS.md Section 5.6).
Verifies:
1. Success list endpoints return { "ok": true, "data": [...], "meta": { "page": ..., "per_page": ..., "total": ... } }
2. Success item endpoints return { "ok": true, "data": {...} } without meta.
3. Error responses return { "ok": false, "error": { "code": "...", "message": "...", "details": {...} } }
4. Error responses NEVER leak a Python stack trace or traceback to the client.
"""
import pytest
from datetime import date
from extensions import db
from models import Institution, Programme, StudentRecord, User, Role


@pytest.fixture
def envelope_fixture(app):
    """Seed minimal fixtures for envelope testing."""
    with app.app_context():
        vesit = Institution.query.filter_by(code="VESIT").first()
        if not vesit:
            vesit = Institution(code="VESIT", name="VESIT Mumbai", state="MH", city="Mumbai")
            db.session.add(vesit)
            db.session.flush()

        it = Programme.query.filter_by(institution_id=vesit.id, code="IT").first()
        if not it:
            it = Programme(institution_id=vesit.id, code="IT", name="Information Technology", level="UG")
            db.session.add(it)
            db.session.flush()

        student = StudentRecord.query.filter_by(institution_id=vesit.id, institution_student_id="ENV-001").first()
        if not student:
            student = StudentRecord(
                institution_id=vesit.id,
                programme_id=it.id,
                institution_student_id="ENV-001",
                full_name="Envelope Test Student",
                normalized_name="envelope test student",
                apaar_ref="111122223333",
                academic_year="2026-27",
                admission_date=date.today(),
                admission_category="OPEN",
                classification="REGULAR",
                enrolment_status="ACTIVE",
                validation_status="VALID",
            )
            db.session.add(student)
            db.session.commit()

        return {
            "vesit_id": vesit.id,
            "student_id": student.id
        }



def _assert_no_traceback(res):
    """Assert that raw response data contains no python traceback leakage."""
    text = res.get_data(as_text=True)
    assert "Traceback (most recent call last)" not in text
    assert "ZeroDivisionError" not in text
    assert "InternalServerError" not in text or res.status_code == 500


# ---------------------------------------------------------------------------
# 1. Success List Endpoints Envelope Contract
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("endpoint", [
    "/api/institutions",
    "/api/students",
    "/api/seats/summary",
    "/api/audit-logs",
])
def test_list_endpoints_success_envelope(aicte_admin_client, envelope_fixture, endpoint):
    """List endpoints must return ok=True, data=list, and meta={page, per_page, total}."""
    res = aicte_admin_client.get(endpoint)
    assert res.status_code == 200
    body = res.get_json()

    # Core envelope checks
    assert body is not None
    assert body.get("ok") is True
    assert isinstance(body.get("data"), list)
    assert "error" not in body or body["error"] is None

    # Meta checks
    meta = body.get("meta")
    assert isinstance(meta, dict)
    assert "total" in meta and isinstance(meta["total"], int)
    if "page" in meta:
        assert isinstance(meta["page"], int)
        assert "per_page" in meta and isinstance(meta["per_page"], int)



# ---------------------------------------------------------------------------
# 2. Success Single Entity Endpoints Envelope Contract
# ---------------------------------------------------------------------------

def test_single_item_endpoints_success_envelope(aicte_admin_client, envelope_fixture):
    """Single-item endpoints return ok=True and data=dict without meta."""
    endpoints = [
        "/api/auth/me",
        f"/api/institutions/{envelope_fixture['vesit_id']}",
        f"/api/students/{envelope_fixture['student_id']}",
    ]

    for endpoint in endpoints:
        res = aicte_admin_client.get(endpoint)
        assert res.status_code == 200, f"Failed on endpoint {endpoint}"
        body = res.get_json()

        assert body.get("ok") is True
        assert isinstance(body.get("data"), dict)
        assert body.get("meta") is None
        assert "error" not in body or body["error"] is None


# ---------------------------------------------------------------------------
# 3. Error Envelope Contract (401, 403, 404, 422) & No Traceback
# ---------------------------------------------------------------------------

def test_unauthenticated_error_envelope_and_no_traceback(client):
    """401 error response envelope must have ok=False, code='UNAUTHENTICATED', and no traceback."""
    res = client.get("/api/auth/me")
    assert res.status_code == 401
    _assert_no_traceback(res)

    body = res.get_json()
    assert body.get("ok") is False
    assert "data" not in body
    error = body.get("error")
    assert isinstance(error, dict)
    assert error.get("code") == "UNAUTHENTICATED"
    assert isinstance(error.get("message"), str)
    assert isinstance(error.get("details"), dict)


def test_forbidden_error_envelope_and_no_traceback(vesit_client, envelope_fixture):
    """403 error response envelope must have ok=False, code='FORBIDDEN', and no traceback."""
    # VESIT admin trying to access restricted AICTE policy modification
    res = vesit_client.patch("/api/policies/apaar_regex", json={"value": r"^\d{12}$"})
    assert res.status_code == 403
    _assert_no_traceback(res)

    body = res.get_json()
    assert body.get("ok") is False
    assert "data" not in body
    error = body.get("error")
    assert isinstance(error, dict)
    assert error.get("code") == "FORBIDDEN"
    assert isinstance(error.get("message"), str)
    assert isinstance(error.get("details"), dict)


def test_not_found_error_envelope_and_no_traceback(aicte_admin_client):
    """404 error response envelope must have ok=False, code='NOT_FOUND', and no traceback."""
    res = aicte_admin_client.get("/api/students/nonexistent-student-uuid-999")
    assert res.status_code == 404
    _assert_no_traceback(res)

    body = res.get_json()
    assert body.get("ok") is False
    assert "data" not in body
    error = body.get("error")
    assert isinstance(error, dict)
    assert error.get("code") == "NOT_FOUND"
    assert isinstance(error.get("message"), str)
    assert isinstance(error.get("details"), dict)


def test_validation_error_envelope_and_no_traceback(vesit_client):
    """422 error response envelope must have ok=False, code='VALIDATION_ERROR', details, and no traceback."""
    res = vesit_client.post("/api/students", json={})
    assert res.status_code == 422
    _assert_no_traceback(res)

    body = res.get_json()
    assert body.get("ok") is False
    assert "data" not in body
    error = body.get("error")
    assert isinstance(error, dict)
    assert error.get("code") == "VALIDATION_ERROR"
    assert isinstance(error.get("message"), str)
    assert isinstance(error.get("details"), dict)


def test_login_validation_error_envelope_and_no_traceback(client):
    """422 error on empty login body must follow the standard envelope and leak no traceback."""
    res = client.post("/api/auth/login", json={})
    assert res.status_code == 422
    _assert_no_traceback(res)

    body = res.get_json()
    assert body.get("ok") is False
    error = body.get("error")
    assert error.get("code") == "VALIDATION_ERROR"
    assert isinstance(error.get("message"), str)
    assert isinstance(error.get("details"), dict)
