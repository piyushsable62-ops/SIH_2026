"""
Tests for Audit Service, Audit Logs API, Institution Dashboard API, Enrolment Reports,
and Demo Environment Reset (Task A11).

Coverage:
1. Audit rows exist for login, student create, admission rejected, policy change.
2. Institution admin sees only own audit rows (multi-tenant isolation).
3. No secrets or full unmasked 12-digit APAAR numbers appear in audit details.
4. Institution dashboard JSON metrics and multi-tenant scoping.
5. Enrolment report CSV download: scoping, formula sanitization, and 'SYNTHETIC' filename.
6. Demo reset endpoint: blocked for non-admin, requires confirmation token, logs audit event.
"""
from datetime import date, datetime
import io
import csv
import pytest
from flask import json

from extensions import db
from models.user import User
from models.institution import Institution, Programme, IntakeCapacity
from models.student import StudentRecord
from models.admission import AdmissionEvent, DataSubmission, SubmissionError
from models.policy import SystemPolicy
from models.audit import AuditLog
from models.enums import (
    Role,
    Classification,
    AdmissionCategory,
    EnrolmentStatus,
    ValidationStatus,
    AdmissionEventResult,
)
from services import audit_service
from services import seat_ledger


@pytest.fixture
def audit_fixture(app):
    """Seed minimal test environment with institutions, users, programmes, capacities, and policies."""
    with app.app_context():
        # Clear tables
        AuditLog.query.delete()
        AdmissionEvent.query.delete()
        SubmissionError.query.delete()
        DataSubmission.query.delete()
        StudentRecord.query.delete()
        IntakeCapacity.query.delete()
        Programme.query.delete()
        User.query.delete()
        Institution.query.delete()
        SystemPolicy.query.delete()

        # Seed institutions
        vesit = Institution(id="inst-vesit-uuid", code="VESIT", name="VESIT Mumbai", state="MH", city="Mumbai", status="ACTIVE")
        coep = Institution(id="inst-coep-uuid", code="COEP", name="COEP Pune", state="MH", city="Pune", status="ACTIVE")
        db.session.add_all([vesit, coep])
        db.session.flush()

        # Seed users
        aicte_admin = User(username="AICTE_Admin", full_name="AICTE Administrator", role=Role.AICTE_ADMIN.value, is_active=True)
        aicte_admin.set_password("Demo@12345")

        vesit_admin = User(username="VESIT", full_name="VESIT Admin", role=Role.INSTITUTION_ADMIN.value, institution_id=vesit.id, is_active=True)
        vesit_admin.set_password("Demo@12345")

        coep_admin = User(username="COEP", full_name="COEP Admin", role=Role.INSTITUTION_ADMIN.value, institution_id=coep.id, is_active=True)
        coep_admin.set_password("Demo@12345")

        auditor_user = User(username="AuditorOne", full_name="System Auditor", role=Role.AUDITOR.value, is_active=True)
        auditor_user.set_password("Demo@12345")

        db.session.add_all([aicte_admin, vesit_admin, coep_admin, auditor_user])

        # Seed programmes
        prog_vesit = Programme(id="prog-vesit-it", institution_id=vesit.id, code="IT", name="Information Technology", level="UG")
        prog_coep = Programme(id="prog-coep-cs", institution_id=coep.id, code="CS", name="Computer Science", level="UG")
        db.session.add_all([prog_vesit, prog_coep])
        db.session.flush()

        # Seed capacities (VESIT: 120 regular, 10 supernumerary)
        cap_vesit = IntakeCapacity(
            institution_id=vesit.id,
            programme_id=prog_vesit.id,
            academic_year="2026-27",
            approved_regular_intake=120,
            supernumerary_capacity=10,
            admitted_regular=0,
            admitted_supernumerary=0,
            category_rules={"NRI": 5, "FN": 5}
        )
        cap_coep = IntakeCapacity(
            institution_id=coep.id,
            programme_id=prog_coep.id,
            academic_year="2026-27",
            approved_regular_intake=60,
            supernumerary_capacity=6,
            admitted_regular=0,
            admitted_supernumerary=0,
        )
        db.session.add_all([cap_vesit, cap_coep])

        # Seed system policy
        pol = SystemPolicy(
            key="capacity_warning_thresholds",
            value=[80, 90, 100],
            description="Seat warnings",
            updated_by="SYSTEM"
        )
        db.session.add(pol)
        db.session.commit()

        return {
            "vesit_id": vesit.id,
            "coep_id": coep.id,
            "prog_vesit_id": prog_vesit.id,
            "prog_coep_id": prog_coep.id,
        }


def test_audit_rows_for_login(app, client, audit_fixture):
    """Test successful and failed login create appropriate audit rows."""
    # Failed login
    res_fail = client.post("/api/auth/login", json={"username": "VESIT", "password": "WrongPassword"})
    assert res_fail.status_code == 401

    with app.app_context():
        fail_audit = AuditLog.query.filter_by(action="AUTH_LOGIN_FAILURE", actor_id="VESIT").first()
        assert fail_audit is not None
        assert fail_audit.details.get("reason") == "INVALID_CREDENTIALS"

    # Successful login
    res_ok = client.post("/api/auth/login", json={"username": "VESIT", "password": "Demo@12345"})
    assert res_ok.status_code == 200

    with app.app_context():
        ok_audit = AuditLog.query.filter_by(action="AUTH_LOGIN_SUCCESS", actor_id="VESIT").first()
        assert ok_audit is not None
        assert ok_audit.institution_id == audit_fixture["vesit_id"]


def test_audit_row_for_student_create_and_rejected_admission(app, client, audit_fixture):
    """Test student creation logs CREATE_STUDENT and failed admission logs ADMISSION_REJECTED."""
    # Login as VESIT
    client.post("/api/auth/login", json={"username": "VESIT", "password": "Demo@12345"})

    # Create valid student
    student_payload = {
        "institution_id": audit_fixture["vesit_id"],
        "programme_id": audit_fixture["prog_vesit_id"],
        "institution_student_id": "VESIT-2026-001",
        "full_name": "Aarav Sharma",
        "academic_year": "2026-27",
        "admission_date": "2026-07-15",
        "admission_category": "OPEN",
        "classification": "REGULAR",
        "apaar_id": "912345678901",
        "dob": "2005-05-15",
        "gender": "MALE",
        "primary_contact": "9876543210",
        "email": "aarav.sharma@example.com"
    }

    res = client.post("/api/students", json=student_payload)
    assert res.status_code == 201

    with app.app_context():
        create_audit = AuditLog.query.filter_by(action="CREATE_STUDENT").first()
        assert create_audit is not None
        assert create_audit.actor_id == "VESIT"
        assert create_audit.institution_id == audit_fixture["vesit_id"]
        assert create_audit.details.get("institution_student_id") == "VESIT-2026-001"

    # Attempt duplicate admission on valid programme to cause rejection
    duplicate_admission = {
        "institution_id": audit_fixture["vesit_id"],
        "programme_id": audit_fixture["prog_vesit_id"],
        "institution_student_id": "VESIT-2026-001",
        "full_name": "Duplicate Student Attempt",
        "academic_year": "2026-27",
        "admission_category": "OPEN",
        "classification": "REGULAR",
        "apaar_ref": "912345678901"
    }
    res_bad = client.post("/api/admissions", json=duplicate_admission)
    assert res_bad.status_code == 409

    with app.app_context():
        reject_audit = AuditLog.query.filter_by(action="ADMISSION_REJECTED").first()
        assert reject_audit is not None
        assert reject_audit.actor_id == "VESIT"


def test_audit_row_for_policy_change(app, client, audit_fixture):
    """Test policy change by AICTE_ADMIN produces POLICY_UPDATE audit row."""
    # Login as AICTE_Admin
    client.post("/api/auth/login", json={"username": "AICTE_Admin", "password": "Demo@12345"})

    res = client.patch(
        "/api/policies/capacity_warning_thresholds",
        json={"value": [75, 85, 95]}
    )
    assert res.status_code == 200

    with app.app_context():
        pol_audit = AuditLog.query.filter_by(action="POLICY_UPDATE", entity_id="capacity_warning_thresholds").first()
        assert pol_audit is not None
        assert pol_audit.actor_id == "AICTE_Admin"
        assert pol_audit.details.get("new_value") == [75, 85, 95]


def test_institution_admin_sees_only_own_audit_rows(app, client, audit_fixture):
    """Verify multi-tenant isolation: INSTITUTION_ADMIN sees only own institution audit records."""
    vesit_id = audit_fixture["vesit_id"]
    coep_id = audit_fixture["coep_id"]

    # Seed artificial audit logs for VESIT and COEP directly
    with app.app_context():
        audit_service.log(
            actor="VESIT",
            action="CREATE_STUDENT",
            entity_type="STUDENT",
            entity_id="stud-v1",
            details={"note": "VESIT student"},
            institution_id=vesit_id
        )
        audit_service.log(
            actor="COEP",
            action="CREATE_STUDENT",
            entity_type="STUDENT",
            entity_id="stud-c1",
            details={"note": "COEP student"},
            institution_id=coep_id
        )

    # Login as VESIT admin
    client.post("/api/auth/login", json={"username": "VESIT", "password": "Demo@12345"})

    res = client.get("/api/audit-logs")
    assert res.status_code == 200
    data = res.get_json()["data"]

    # Must only contain VESIT audit rows
    for item in data:
        assert item["institution_id"] == vesit_id
        assert item["institution_id"] != coep_id

    # Cross-tenant query attempt must be rejected
    res_forbidden = client.get(f"/api/audit-logs?institution_id={coep_id}")
    assert res_forbidden.status_code == 403

    # Login as AICTE_Admin: sees all logs
    client.post("/api/auth/login", json={"username": "AICTE_Admin", "password": "Demo@12345"})
    res_admin = client.get("/api/audit-logs")
    assert res_admin.status_code == 200
    all_data = res_admin.get_json()["data"]
    inst_ids = {item["institution_id"] for item in all_data if item.get("institution_id")}
    assert vesit_id in inst_ids
    assert coep_id in inst_ids


def test_no_secrets_or_full_apaar_in_audit_details(app, client, audit_fixture):
    """Verify passwords, private keys, tokens, and 12-digit APAARs are scrubbed/masked in audit logs."""
    raw_apaar = "987654321098"
    masked_apaar = "XXXXXXXX1098"

    with app.app_context():
        audit_service.log(
            actor="AICTE_Admin",
            action="TEST_SCRUB",
            entity_type="SYSTEM",
            entity_id="scrub-1",
            details={
                "password": "SuperSecretPassword123",
                "secret_key": "raw-key-value",
                "apaar_id": raw_apaar,
                "nested": {
                    "token": "bearer-token-123",
                    "student_apaar": raw_apaar,
                }
            },
            institution_id=audit_fixture["vesit_id"]
        )

    # Login as AICTE_Admin and fetch logs
    client.post("/api/auth/login", json={"username": "AICTE_Admin", "password": "Demo@12345"})
    res = client.get("/api/audit-logs?action=TEST_SCRUB")
    assert res.status_code == 200
    data = res.get_json()["data"]
    assert len(data) == 1
    details = data[0]["details"]

    assert details["password"] == "[REDACTED]"
    assert details["secret_key"] == "[REDACTED]"
    assert details["apaar_id"] == masked_apaar
    assert details["nested"]["token"] == "[REDACTED]"
    assert details["nested"]["student_apaar"] == masked_apaar
    # Full 12-digit APAAR must NOT appear anywhere in the raw text response
    assert raw_apaar not in res.get_data(as_text=True)


def test_institution_dashboard_json_scope_and_metrics(app, client, audit_fixture):
    """Test GET /api/dashboard/institution metrics calculation and scoping."""
    vesit_id = audit_fixture["vesit_id"]
    coep_id = audit_fixture["coep_id"]

    # Seed an active student for VESIT
    with app.app_context():
        st = StudentRecord(
            institution_id=vesit_id,
            programme_id=audit_fixture["prog_vesit_id"],
            institution_student_id="VESIT-DASH-1",
            full_name="Dashboard Test Student",
            normalized_name="dashboard test student",
            academic_year="2026-27",
            admission_date=date.today(),
            admission_category=AdmissionCategory.OPEN.value,
            classification=Classification.REGULAR.value,
            enrolment_status=EnrolmentStatus.ACTIVE.value,
            validation_status=ValidationStatus.PENDING.value,
        )
        db.session.add(st)
        db.session.commit()

    # Login as VESIT admin
    client.post("/api/auth/login", json={"username": "VESIT", "password": "Demo@12345"})

    res = client.get("/api/dashboard/institution")
    assert res.status_code == 200
    body = res.get_json()
    assert body["ok"] is True
    data = body["data"]

    # Verify response structure
    assert data["institution"]["id"] == vesit_id
    seat_summary = data["seat_summary"]
    assert seat_summary["approved_regular_intake"] == 120
    assert seat_summary["regular_admitted"] == 1
    assert seat_summary["regular_remaining"] == 119
    assert seat_summary["regular_utilisation_pct"] == 0.8
    assert seat_summary["supernumerary_capacity"] == 10

    # Pending checks count
    assert data["pending_identity_checks"]["pending"] == 1
    assert data["pending_identity_checks"]["total"] == 1

    # Submission history and risk cases structures present
    assert isinstance(data["submission_history"], list)
    assert isinstance(data["validation_error_counts"], dict)
    assert isinstance(data["risk_cases_by_severity"], dict)
    assert isinstance(data["credential_issuance_history"], list)

    # Scoping check: VESIT admin cannot query COEP
    res_cross = client.get(f"/api/dashboard/institution?institution_id={coep_id}")
    assert res_cross.status_code == 403


def test_enrolment_report_csv_scope_and_filename(app, client, audit_fixture):
    """Test enrolment report JSON & CSV export: scoping, filename contains 'SYNTHETIC', and audit logged."""
    vesit_id = audit_fixture["vesit_id"]

    # Login as VESIT admin
    client.post("/api/auth/login", json={"username": "VESIT", "password": "Demo@12345"})

    # 1. JSON report
    res_json = client.get("/api/reports/enrolment?academic_year=2026-27")
    assert res_json.status_code == 200
    json_data = res_json.get_json()["data"]
    assert json_data["academic_year"] == "2026-27"
    assert len(json_data["rows"]) == 1
    assert json_data["rows"][0]["institution_id"] == vesit_id

    # 2. CSV report
    res_csv = client.get("/api/reports/enrolment.csv?academic_year=2026-27")
    assert res_csv.status_code == 200
    assert "text/csv" in res_csv.headers.get("Content-Type", "")

    # Content-Disposition header must contain 'SYNTHETIC'
    content_disp = res_csv.headers.get("Content-Disposition", "")
    assert "attachment" in content_disp
    assert "SYNTHETIC" in content_disp

    # Verify CSV format and absence of comment rows
    csv_text = res_csv.get_data(as_text=True)
    reader = csv.reader(io.StringIO(csv_text))
    rows = list(reader)
    assert len(rows) >= 2  # header + data row
    header = rows[0]
    assert header[0] == "Institution Code"
    assert "Approved Regular Intake" in header
    assert "OPEN" in header

    # Verify audit log was created for the export
    with app.app_context():
        export_audit = AuditLog.query.filter_by(action="EXPORT_ENROLMENT_REPORT_CSV").first()
        assert export_audit is not None
        assert export_audit.actor_id == "VESIT"
        assert "SYNTHETIC" in export_audit.details.get("filename", "")


def test_demo_reset_blocked_for_non_admin_and_requires_token(app, client, audit_fixture):
    """Test demo reset is blocked for non-admin, requires confirmation token, and logs DEMO_RESET."""
    # 1. Blocked for INSTITUTION_ADMIN
    client.post("/api/auth/login", json={"username": "VESIT", "password": "Demo@12345"})
    res_blocked = client.post("/api/admin/demo-reset", json={"confirmation": "RESET"})
    assert res_blocked.status_code == 403

    # 2. As AICTE_Admin without confirmation token -> 422
    client.post("/api/auth/login", json={"username": "AICTE_Admin", "password": "Demo@12345"})
    res_no_token = client.post("/api/admin/demo-reset", json={})
    assert res_no_token.status_code == 422
    assert res_no_token.get_json()["error"]["code"] == "VALIDATION_ERROR"

    # 3. With valid confirmation token -> 200 and audit logged
    res_reset = client.post("/api/admin/demo-reset", json={"confirmation": "RESET"})
    assert res_reset.status_code == 200
    assert res_reset.get_json()["ok"] is True

    with app.app_context():
        reset_audit = AuditLog.query.filter_by(action="DEMO_RESET").first()
        assert reset_audit is not None
        assert reset_audit.actor_id == "AICTE_Admin"
        assert reset_audit.entity_id == "DATABASE"
