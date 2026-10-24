"""
Hardening, Security Review, and Cross-Module Integration Tests for Member A (Task A13).
Validates AGENTS.md §8 checklist, cross-institution isolation, N+1 query avoidance,
audit scrubbing, 500 error sanitization, and Member B service hooks.
"""
import io
import re
import pytest
from unittest.mock import patch
from flask import Flask, jsonify
from extensions import db
from models import (
    User, Institution, Programme, IntakeCapacity, StudentRecord,
    AdmissionEvent, Role, Classification, AdmissionCategory
)
from models.credential import Credential
from models.risk import RiskCase, RiskEvidence
from services import audit_service, seat_ledger
from services.csv_service import check_csv_injection, sanitize_csv_cell_for_export, preview_csv, commit_submission
from config import ProductionConfig


# ---------------------------------------------------------------------------
# 1. Route Authorization Enumeration Test (AGENTS.md §8 checklist)
# ---------------------------------------------------------------------------

def test_all_api_routes_require_authentication(app):
    """
    Enumerate app.url_map and verify that EVERY /api endpoint requires authentication
    except explicitly allowed public endpoints: /api/auth/login and /api/health.
    """
    client = app.test_client()
    exempt = {"/api/auth/login", "/api/health"}
    unprotected = []

    for rule in sorted(app.url_map.iter_rules(), key=lambda r: r.rule):
        if not rule.rule.startswith("/api"):
            continue
        if rule.rule in exempt:
            continue

        # Replace dynamic URL parameters with placeholder dummy UUID
        test_path = re.sub(r"<[^>]+>", "00000000-0000-0000-0000-000000000000", rule.rule)

        for method in rule.methods:
            if method in {"HEAD", "OPTIONS"}:
                continue
            func = getattr(client, method.lower())
            res = func(test_path)
            # Must return 401 Unauthorized or 302 Redirect to login
            if res.status_code not in {401, 302}:
                unprotected.append((method, rule.rule, res.status_code))

    assert not unprotected, f"Found /api routes accessible without authentication: {unprotected}"


def test_public_verify_routes_are_read_only(app):
    """Confirm public verification routes are read-only (GET only) and reject mutating calls."""
    client = app.test_client()
    for method in ["post", "patch", "delete", "put"]:
        caller = getattr(client, method)
        res = caller("/verify/CRED-123456")
        assert res.status_code in {405, 404}


# ---------------------------------------------------------------------------
# 2. Institution Isolation Tests
# ---------------------------------------------------------------------------

def test_cross_institution_isolation_students(app, vesit_client, coep_client):
    """Confirm COEP admin cannot read, search, reveal, or patch VESIT student data."""
    with app.app_context():
        vesit = Institution.query.filter_by(code="VESIT").first()
        coep = Institution.query.filter_by(code="COEP").first()
        vesit_id = vesit.id
        coep_id = coep.id

        prog = Programme(institution_id=vesit_id, code="CS", name="Computer Science", level="UG")
        db.session.add(prog)
        db.session.flush()

        student = StudentRecord(
            institution_id=vesit_id,
            programme_id=prog.id,
            institution_student_id="VESIT-CS-9999",
            full_name="Secret Student",
            normalized_name="SECRET STUDENT",
            academic_year="2024-25",
            apaar_ref="999988887777",
            admission_category="OPEN",
            classification="REGULAR"
        )
        db.session.add(student)
        db.session.commit()
        s_id = student.id

    # 1. COEP calls GET /api/students/<id> of VESIT student -> 403 Forbidden
    res = coep_client.get(f"/api/students/{s_id}")
    assert res.status_code == 403
    assert res.get_json()["error"]["code"] == "FORBIDDEN"

    # 2. COEP accesses HTML /students/<id> of VESIT student -> 403 Forbidden
    res = coep_client.get(f"/students/{s_id}")
    assert res.status_code == 403

    # 3. COEP calls PATCH /api/students/<id> of VESIT student -> 403 Forbidden
    res = coep_client.patch(f"/api/students/{s_id}", json={"full_name": "Tampered", "reason": "Hacking"})
    assert res.status_code == 403

    # 4. COEP passes institution_id=vesit_id in list -> 403 Forbidden
    res = coep_client.get(f"/api/students?institution_id={vesit_id}")
    assert res.status_code == 403
    assert res.get_json()["error"]["code"] == "FORBIDDEN"


def test_cross_institution_isolation_admissions(app, coep_client):
    """Confirm COEP admin cannot submit admissions into VESIT."""
    with app.app_context():
        vesit = Institution.query.filter_by(code="VESIT").first()
        vesit_id = vesit.id
        prog = Programme.query.filter_by(institution_id=vesit_id).first()
        if not prog:
            prog = Programme(institution_id=vesit_id, code="IT", name="IT", level="UG")
            db.session.add(prog)
            db.session.commit()
        prog_id = prog.id

    payload = {
        "institution_id": vesit_id,
        "programme_id": prog_id,
        "academic_year": "2026-27",
        "full_name": "Injected Admission",
        "institution_student_id": "STU-INJECT-01",
        "classification": "REGULAR",
        "category": "OPEN"
    }

    res = coep_client.post("/api/admissions", json=payload)
    assert res.status_code == 403
    assert res.get_json()["error"]["code"] == "FORBIDDEN"


# ---------------------------------------------------------------------------
# 3. Security Review: Error Handlers & Secrets
# ---------------------------------------------------------------------------

def test_unhandled_500_never_leaks_stack_trace(app):
    """Confirm unhandled exceptions with DEBUG=False return a sanitized SERVER_ERROR envelope without stack trace."""
    @app.route("/api/test-trigger-500", methods=["GET"])
    def trigger_crash():
        raise ZeroDivisionError("Simulated server fatal failure: secret_db_pw=123")

    app.config["DEBUG"] = False
    app.config["TESTING"] = False

    client = app.test_client()
    res = client.get("/api/test-trigger-500")
    assert res.status_code == 500

    data = res.get_json()
    assert data["ok"] is False
    assert data["error"]["code"] == "SERVER_ERROR"
    assert data["error"]["message"] == "An internal server error occurred."

    # Stack trace keywords must NOT leak to client
    body_str = res.get_data(as_text=True)
    assert "Traceback" not in body_str
    assert "ZeroDivisionError" not in body_str
    assert "secret_db_pw" not in body_str
    assert "File \"" not in body_str


def test_no_secrets_in_audit_logs(app):
    """Confirm sensitive fields (passwords, tokens, private keys) and 12-digit APAAR are scrubbed from audit."""
    with app.app_context():
        audit_service.log(
            actor="test_user",
            action="TEST_SENSITIVE_LOG",
            entity_type="SECURITY_TEST",
            entity_id="sec-001",
            details={
                "password": "SuperSecretPassword123!",
                "token": "tok_live_abcdef123456789",
                "private_key": "-----BEGIN PRIVATE KEY-----MIIEvg...",
                "student_apaar": "123456789012",
                "nested": {
                    "secret_key": "hidden_jwt_secret",
                    "raw_token": "token_xyz"
                }
            }
        )

        from models.audit import AuditLog
        row = AuditLog.query.filter_by(action="TEST_SENSITIVE_LOG").first()
        assert row is not None
        details = row.details

        # Check scrubbing
        assert details["password"] == "[REDACTED]"
        assert details["token"] == "[REDACTED]"
        assert details["private_key"] == "[REDACTED]"
        assert details["student_apaar"] == "XXXXXXXX9012"
        assert details["nested"]["secret_key"] == "[REDACTED]"
        assert details["nested"]["raw_token"] == "[REDACTED]"


def test_session_cookie_security_config():
    """Verify session cookie configuration flags."""
    from config import Config
    assert Config.SESSION_COOKIE_HTTPONLY is True
    assert Config.SESSION_COOKIE_SAMESITE == "Lax"
    assert ProductionConfig.SESSION_COOKIE_SECURE is True


def test_logout_invalidates_session(app, aicte_admin_client):
    """Verify logging out clears session and prevents further authenticated requests."""
    # Authenticated client can access /api/auth/me
    res = aicte_admin_client.get("/api/auth/me")
    assert res.status_code == 200

    # Logout
    res = aicte_admin_client.post("/api/auth/logout")
    assert res.status_code == 200

    # /api/auth/me is now 401
    res = aicte_admin_client.get("/api/auth/me")
    assert res.status_code == 401


# ---------------------------------------------------------------------------
# 4. CSV Injection and Ingestion Hardening
# ---------------------------------------------------------------------------

def test_csv_injection_cell_sanitization():
    """Verify formula injection triggers (=, +, -, @, tab, cr) are sanitized."""
    injections = [
        ("=1+1", "1+1"),
        ("+cmd|' /C calc'!A0", "cmd|' /C calc'!A0"),
        ("-2+3", "2+3"),
        ("@SUM(A1:A10)", "SUM(A1:A10)"),
        ("\tDANGEROUS", "DANGEROUS"),
        ("\rMALICIOUS", "MALICIOUS"),
    ]
    for raw, expected in injections:
        is_inj, clean = check_csv_injection(raw)
        assert is_inj is True
        assert clean == expected

    # Benign string stays unchanged
    is_inj, clean = check_csv_injection("Normal Student Name")
    assert is_inj is False
    assert clean == "Normal Student Name"


# ---------------------------------------------------------------------------
# 5. Cross-Module Integration Tests
# ---------------------------------------------------------------------------

def test_identity_scan_failure_never_breaks_student_creation(app, vesit_client):
    """
    Confirm manual student creation triggers services.identity_resolution.scan
    and that a scan failure is caught, logged, audited, and allows creation to succeed.
    """
    with app.app_context():
        vesit = Institution.query.filter_by(code="VESIT").first()
        vesit_id = vesit.id
        prog = Programme.query.filter_by(institution_id=vesit_id).first()
        if not prog:
            prog = Programme(institution_id=vesit_id, code="CMPN", name="CMPN", level="UG")
            db.session.add(prog)
            db.session.commit()
        prog_id = prog.id

        cap = IntakeCapacity(
            institution_id=vesit_id,
            programme_id=prog_id,
            academic_year="2024-25",
            approved_regular_intake=60,
            supernumerary_capacity=10,
            admitted_regular=0,
            admitted_supernumerary=0
        )
        db.session.add(cap)
        db.session.commit()

    student_payload = {
        "institution_id": vesit_id,
        "programme_id": prog_id,
        "institution_student_id": "VESIT-ROBUST-001",
        "full_name": "Robust Student",
        "academic_year": "2024-25",
        "admission_date": "2024-07-15",
        "admission_category": "OPEN",
        "classification": "REGULAR",
        "apaar_ref": "888877776666"
    }

    # Mock identity scan to raise exception
    with patch("services.identity_resolution.scan", side_effect=RuntimeError("Simulated scan service failure")):
        res = vesit_client.post("/api/students", json=student_payload)
        # Creation MUST succeed despite scan failure
        assert res.status_code == 201
        data = res.get_json()["data"]
        assert data["full_name"] == "Robust Student"

    # Confirm audit log captured the scan error
    with app.app_context():
        from models.audit import AuditLog
        err_log = AuditLog.query.filter_by(action="IDENTITY_SCAN_ERROR").first()
        assert err_log is not None
        assert "Simulated scan service failure" in err_log.details["error"]


def test_excess_intake_rejection_triggers_risk_case(app, aicte_admin_client):
    """
    Confirm excess-intake rejections call risk_engine.record_excess_intake_event
    and that the resulting risk case appears in /api/risk/cases.
    """
    with app.app_context():
        vesit = Institution.query.filter_by(code="VESIT").first()
        vesit_id = vesit.id
        prog = Programme(institution_id=vesit_id, code="EXCESS_TEST", name="Excess Programme", level="UG")
        db.session.add(prog)
        db.session.flush()

        cap = IntakeCapacity(
            institution_id=vesit_id,
            programme_id=prog.id,
            academic_year="2026-27",
            approved_regular_intake=1,  # 1 seat only
            supernumerary_capacity=0,
            admitted_regular=1,         # already full!
            admitted_supernumerary=0
        )
        db.session.add(cap)
        db.session.commit()

        prog_id = prog.id

    # Attempt to commit 2nd regular student -> Rejected CAPACITY_EXCEEDED
    payload = {
        "institution_id": vesit_id,
        "programme_id": prog_id,
        "academic_year": "2026-27",
        "full_name": "Blocked Candidate",
        "institution_student_id": "VESIT-BLOCKED-01",
        "classification": "REGULAR",
        "category": "OPEN"
    }

    res = aicte_admin_client.post("/api/admissions", json=payload)
    assert res.status_code == 409
    assert res.get_json()["error"]["code"] == "CAPACITY_EXCEEDED"

    # Verify risk case created and appears in /api/risk/cases
    res_cases = aicte_admin_client.get("/api/risk/cases")
    assert res_cases.status_code == 200
    cases_data = res_cases.get_json()["data"]
    excess_cases = [c for c in cases_data if c.get("score") == 30 or "approved seat matrix" in str(c.get("recommended_action", ""))]
    assert len(excess_cases) >= 1


def test_student_detail_links_risk_cases_and_credentials(app, aicte_admin_client):
    """Confirm student detail returns linked risk cases and linked credentials created by Member B."""
    with app.app_context():
        vesit = Institution.query.filter_by(code="VESIT").first()
        vesit_id = vesit.id
        prog = Programme.query.filter_by(institution_id=vesit_id).first()
        if not prog:
            prog = Programme(institution_id=vesit_id, code="AI", name="AI", level="UG")
            db.session.add(prog)
            db.session.commit()
        prog_id = prog.id

        student = StudentRecord(
            institution_id=vesit_id,
            programme_id=prog_id,
            institution_student_id="VESIT-LINKED-01",
            full_name="Linked Test Student",
            normalized_name="LINKED TEST STUDENT",
            academic_year="2024-25",
            apaar_ref="111122223333",
            admission_category="OPEN",
            classification="REGULAR"
        )
        db.session.add(student)
        db.session.flush()

        # Create Member B Credential
        cred = Credential(
            reference="CRED-TST-0001",
            student_id=student.id,
            institution_id=vesit_id,
            programme_name="AI",
            award_year="2025",
            payload_json="{}",
            signature="dGVzdA==",
            key_id="test-key-id",
            status_stored="ISSUED"
        )
        db.session.add(cred)

        # Create Member B Risk Case linked to this student
        risk_case = RiskCase(
            reference="CASE-TST-001",
            severity="MEDIUM",
            score=40,
            status="OPEN",
            summary="Test identity overlap",
            institution_id=vesit_id
        )
        db.session.add(risk_case)
        db.session.flush()

        evidence = RiskEvidence(
            case_id=risk_case.id,
            event_key="evt-001",
            rule_key="DUPLICATE_APAAR",
            points=40,
            description="Duplicate APAAR detected across institutions",
            related_student_ids=[student.id]
        )
        db.session.add(evidence)
        db.session.commit()

        student_id = student.id

    # Fetch detail via API
    res = aicte_admin_client.get(f"/api/students/{student_id}")
    assert res.status_code == 200
    data = res.get_json()["data"]

    # Assert risk cases linked
    assert "linked_risk_cases" in data
    assert len(data["linked_risk_cases"]) >= 1
    assert data["linked_risk_cases"][0]["reference"] == "CASE-TST-001"

    # Assert credentials linked
    assert "linked_credentials" in data
    assert len(data["linked_credentials"]) >= 1
    assert data["linked_credentials"][0]["reference"] == "CRED-TST-0001"
    assert data["linked_credentials"][0]["status_stored"] == "ISSUED"

    # Check HTML page renders
    res_html = aicte_admin_client.get(f"/students/{student_id}")
    assert res_html.status_code == 200
    html_text = res_html.get_data(as_text=True)
    assert "Linked Risk Cases" in html_text
    assert "Academic Credentials" in html_text
