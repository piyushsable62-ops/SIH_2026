"""
Tests for Student records repository, manual entry, updates, change history, and export (Task A7).
Verifies:
- Creation of valid student records
- Validation error on invalid APAAR format
- History tracking (old/new values, changed_by, reason) on updates
- Multi-tenant institution isolation (read, update, cross-institution scoping)
- Masked APAAR by default in API and CSV export
- Audit-logging for reveal operations and CSV exports
- HTML views for repository and student detail
"""
import pytest
from datetime import date
from extensions import db
from models.institution import Institution, Programme, IntakeCapacity
from models.student import StudentRecord, StudentRecordHistory
from models.user import User
from models.policy import SystemPolicy
from models.audit import AuditLog
from models.enums import Role, Classification, AdmissionCategory, EnrolmentStatus, ValidationStatus


@pytest.fixture
def a7_fixture(app):
    """Seed test institutions, programmes, capacities, demo users, and baseline students."""
    with app.app_context():
        # Clear tables
        db.session.query(StudentRecordHistory).delete()
        db.session.query(StudentRecord).delete()
        db.session.query(IntakeCapacity).delete()
        db.session.query(Programme).delete()
        db.session.query(Institution).delete()
        db.session.query(User).delete()
        db.session.query(SystemPolicy).delete()
        db.session.query(AuditLog).delete()
        db.session.commit()

        # Seed institutions
        vesit = Institution(code="VESIT", name="VESIT Mumbai", state="Maharashtra", city="Mumbai", status="ACTIVE")
        coep = Institution(code="COEP", name="COEP Pune", state="Maharashtra", city="Pune", status="ACTIVE")
        db.session.add_all([vesit, coep])
        db.session.flush()

        # Seed programmes
        vesit_it = Programme(institution_id=vesit.id, code="IT", name="Information Technology", level="UG")
        coep_cs = Programme(institution_id=coep.id, code="CS", name="Computer Science", level="UG")
        db.session.add_all([vesit_it, coep_cs])
        db.session.flush()

        # Seed capacities
        vesit_cap = IntakeCapacity(
            institution_id=vesit.id,
            programme_id=vesit_it.id,
            academic_year="2026-27",
            approved_regular_intake=120,
            supernumerary_capacity=10
        )
        coep_cap = IntakeCapacity(
            institution_id=coep.id,
            programme_id=coep_cs.id,
            academic_year="2026-27",
            approved_regular_intake=120,
            supernumerary_capacity=10
        )
        db.session.add_all([vesit_cap, coep_cap])
        db.session.flush()

        # Seed baseline students
        s_vesit = StudentRecord(
            institution_id=vesit.id,
            programme_id=vesit_it.id,
            institution_student_id="V-2026-001",
            full_name="Aarav Sharma",
            normalized_name="aarav sharma",
            apaar_ref="987654321012",
            academic_year="2026-27",
            admission_date=date(2026, 7, 1),
            admission_category="OPEN",
            classification="REGULAR",
            enrolment_status="ACTIVE",
            validation_status="VALID",
            contact_ref="aarav@vesit.edu",
            fee_payer_ref="FP-TXN-001"
        )
        s_coep = StudentRecord(
            institution_id=coep.id,
            programme_id=coep_cs.id,
            institution_student_id="C-2026-001",
            full_name="Rohan Deshmukh",
            normalized_name="rohan deshmukh",
            apaar_ref="911122223333",
            academic_year="2026-27",
            admission_date=date(2026, 7, 2),
            admission_category="OBC",
            classification="REGULAR",
            enrolment_status="ACTIVE",
            validation_status="VALID",
            contact_ref="rohan@coep.ac.in",
            fee_payer_ref="FP-TXN-002"
        )
        db.session.add_all([s_vesit, s_coep])

        # Seed users
        admin = User(username="AICTE_Admin", full_name="AICTE Admin", role=Role.AICTE_ADMIN.value)
        admin.set_password("Demo@12345")

        vesit_user = User(username="VESIT", full_name="VESIT Admin", role=Role.INSTITUTION_ADMIN.value, institution_id=vesit.id)
        vesit_user.set_password("Demo@12345")

        coep_user = User(username="COEP", full_name="COEP Admin", role=Role.INSTITUTION_ADMIN.value, institution_id=coep.id)
        coep_user.set_password("Demo@12345")

        auditor = User(username="AUDITOR", full_name="Auditor", role=Role.AUDITOR.value)
        auditor.set_password("Demo@12345")

        db.session.add_all([admin, vesit_user, coep_user, auditor])

        # Seed system policies
        pol = SystemPolicy(key="apaar_regex", value="^\\d{12}$", updated_by="SYSTEM")
        db.session.add(pol)

        db.session.commit()

        return {
            "vesit_id": vesit.id,
            "coep_id": coep.id,
            "vesit_it_id": vesit_it.id,
            "coep_cs_id": coep_cs.id,
            "s_vesit_id": s_vesit.id,
            "s_coep_id": s_coep.id
        }


def login(client, username, password="Demo@12345"):
    res = client.post("/api/auth/login", json={"username": username, "password": password})
    assert res.status_code == 200
    return res


# ---------------------------------------------------------------------------
# Test Cases
# ---------------------------------------------------------------------------

def test_create_valid_student(client, a7_fixture):
    """Institution admin can create a valid student record; audit log is written."""
    login(client, "VESIT")

    payload = {
        "institution_student_id": "V-2026-002",
        "full_name": "Priya Nair",
        "apaar_ref": "987654321099",
        "academic_year": "2026-27",
        "institution_id": a7_fixture["vesit_id"],
        "programme_id": a7_fixture["vesit_it_id"],
        "admission_category": "OPEN",
        "classification": "REGULAR",
        "admission_date": "2026-07-05",
        "enrolment_status": "ACTIVE"
    }

    res = client.post("/api/students", json=payload)
    assert res.status_code == 201
    body = res.get_json()
    assert body["ok"] is True
    data = body["data"]
    assert data["institution_student_id"] == "V-2026-002"
    assert data["full_name"] == "Priya Nair"
    # APAAR must be masked in response
    assert data["apaar_ref"] == "XXXXXXXX1099"
    assert data["validation_status"] == "VALID"

    # Verify audit log
    audit_entry = AuditLog.query.filter_by(action="CREATE_STUDENT", entity_id=data["id"]).first()
    assert audit_entry is not None
    assert audit_entry.actor_id == "VESIT"
    assert audit_entry.institution_id == a7_fixture["vesit_id"]


def test_invalid_apaar_rejected_with_row_error(client, a7_fixture):
    """Record with invalid APAAR format is rejected with VALIDATION_ERROR and row code APAAR_FORMAT."""
    login(client, "VESIT")

    payload = {
        "institution_student_id": "V-2026-003",
        "full_name": "Invalid Student",
        "apaar_ref": "12345INVALID",  # Letters and wrong length
        "academic_year": "2026-27",
        "institution_id": a7_fixture["vesit_id"],
        "programme_id": a7_fixture["vesit_it_id"],
        "admission_category": "OPEN",
        "classification": "REGULAR",
        "admission_date": "2026-07-05",
    }

    res = client.post("/api/students", json=payload)
    assert res.status_code == 422
    body = res.get_json()
    assert body["ok"] is False
    assert body["error"]["code"] == "VALIDATION_ERROR"
    errors = body["error"]["details"]["errors"]
    codes = [e["code"] for e in errors]
    assert "APAAR_FORMAT" in codes


def test_update_writes_history_with_old_and_new(client, a7_fixture):
    """Updating student fields writes StudentRecordHistory rows with old/new/changed_by/reason."""
    login(client, "VESIT")
    student_id = a7_fixture["s_vesit_id"]

    # 1. Reject update without reason
    fail_res = client.patch(f"/api/students/{student_id}", json={
        "full_name": "Aarav S. Sharma"
    })
    assert fail_res.status_code == 422
    assert fail_res.get_json()["error"]["code"] == "VALIDATION_ERROR"

    # 2. Successful update with mandatory reason
    res = client.patch(f"/api/students/{student_id}", json={
        "reason": "Official name and contact correction per birth certificate",
        "full_name": "Aarav Suresh Sharma",
        "contact_ref": "aarav.sharma.new@vesit.edu"
    })
    assert res.status_code == 200
    body = res.get_json()
    assert body["ok"] is True
    assert body["changes_count"] == 2

    # Verify history records in DB
    histories = StudentRecordHistory.query.filter_by(student_id=student_id).all()
    assert len(histories) >= 2
    fields = {h.field: h for h in histories}

    assert "full_name" in fields
    assert fields["full_name"].old_value == "Aarav Sharma"
    assert fields["full_name"].new_value == "Aarav Suresh Sharma"
    assert fields["full_name"].changed_by == "VESIT"
    assert "Official name" in fields["full_name"].reason

    assert "contact_ref" in fields
    assert fields["contact_ref"].old_value == "aarav@vesit.edu"
    assert fields["contact_ref"].new_value == "aarav.sharma.new@vesit.edu"


def test_institution_admin_cannot_read_or_update_other_institution_student(client, a7_fixture):
    """Institution admin is forbidden from reading or updating another institution's student."""
    login(client, "VESIT")
    coep_student_id = a7_fixture["s_coep_id"]

    # 1. Forbidden read on detail API
    read_res = client.get(f"/api/students/{coep_student_id}")
    assert read_res.status_code == 403
    assert read_res.get_json()["error"]["code"] == "FORBIDDEN"

    # 2. Forbidden update on detail API
    patch_res = client.patch(f"/api/students/{coep_student_id}", json={
        "reason": "Unauthorized attempt",
        "full_name": "Hacked Name"
    })
    assert patch_res.status_code == 403
    assert patch_res.get_json()["error"]["code"] == "FORBIDDEN"

    # 3. Forbidden creation targeting another institution
    create_res = client.post("/api/students", json={
        "institution_student_id": "CROSS-001",
        "full_name": "Cross Student",
        "apaar_ref": "987654321111",
        "academic_year": "2026-27",
        "institution_id": a7_fixture["coep_id"],  # VESIT trying to target COEP
        "programme_id": a7_fixture["coep_cs_id"],
        "admission_category": "OPEN",
        "classification": "REGULAR",
        "admission_date": "2026-07-05",
    })
    assert create_res.status_code == 403

    # 4. Forbidden query filtering another institution
    query_res = client.get(f"/api/students?institution_id={a7_fixture['coep_id']}")
    assert query_res.status_code == 403


def test_masked_apaar_by_default(client, a7_fixture):
    """Students list and details show APAAR masked by default."""
    login(client, "VESIT")

    # List endpoint
    res_list = client.get("/api/students")
    assert res_list.status_code == 200
    rows = res_list.get_json()["data"]
    assert len(rows) == 1
    assert rows[0]["apaar_ref"] == "XXXXXXXX1012"

    # Detail endpoint
    student_id = a7_fixture["s_vesit_id"]
    res_detail = client.get(f"/api/students/{student_id}")
    assert res_detail.status_code == 200
    assert res_detail.get_json()["data"]["apaar_ref"] == "XXXXXXXX1012"


def test_reveal_apaar_is_audit_logged(client, a7_fixture):
    """Requesting unmasked APAAR with ?reveal=true reveals the identifier and writes an audit log."""
    login(client, "VESIT")
    student_id = a7_fixture["s_vesit_id"]

    # 1. Detail endpoint with reveal=true
    res = client.get(f"/api/students/{student_id}?reveal=true")
    assert res.status_code == 200
    data = res.get_json()["data"]
    assert data["apaar_ref"] == "987654321012"

    # Verify audit log written
    audit_entry = AuditLog.query.filter_by(action="REVEAL_APAAR_RECORD", entity_id=student_id).first()
    assert audit_entry is not None
    assert audit_entry.actor_id == "VESIT"
    assert audit_entry.institution_id == a7_fixture["vesit_id"]

    # 2. List endpoint with reveal=true (AICTE Admin)
    login(client, "AICTE_Admin")
    res_list = client.get("/api/students?reveal=true")
    assert res_list.status_code == 200
    list_data = res_list.get_json()["data"]
    assert any(s["apaar_ref"] == "987654321012" for s in list_data)

    list_audit = AuditLog.query.filter_by(action="REVEAL_APAAR_RECORDS").first()
    assert list_audit is not None
    assert list_audit.actor_id == "AICTE_Admin"


def test_csv_export_respects_scope_and_masks_apaar(client, a7_fixture):
    """CSV export returns masked APAAR, respects institution scope, and is audit-logged."""
    # 1. VESIT Admin export
    login(client, "VESIT")
    res = client.get("/api/students/export")
    assert res.status_code == 200
    assert "text/csv" in res.headers["Content-Type"]
    csv_text = res.data.decode("utf-8")

    # Contains VESIT student
    assert "V-2026-001" in csv_text
    assert "Aarav Sharma" in csv_text
    # APAAR must be masked
    assert "XXXXXXXX1012" in csv_text
    assert "987654321012" not in csv_text
    # Does NOT contain COEP student
    assert "C-2026-001" not in csv_text
    assert "Rohan Deshmukh" not in csv_text

    # Verify export audit entry
    export_audit = AuditLog.query.filter_by(action="EXPORT_STUDENTS_CSV", institution_id=a7_fixture["vesit_id"]).first()
    assert export_audit is not None

    # 2. AICTE Admin export
    login(client, "AICTE_Admin")
    res_aicte = client.get("/api/students/export")
    assert res_aicte.status_code == 200
    aicte_csv = res_aicte.data.decode("utf-8")
    assert "V-2026-001" in aicte_csv
    assert "C-2026-001" in aicte_csv
    assert "XXXXXXXX1012" in aicte_csv
    assert "XXXXXXXX3333" in aicte_csv


def test_html_pages_render(client, a7_fixture):
    """HTML views for /students and /students/<id> render successfully."""
    login(client, "AICTE_Admin")

    # Repository page
    res_repo = client.get("/students")
    assert res_repo.status_code == 200
    assert b"Central Student Repository" in res_repo.data
    assert b"students-table" in res_repo.data

    # Detail page
    student_id = a7_fixture["s_vesit_id"]
    res_detail = client.get(f"/students/{student_id}")
    assert res_detail.status_code == 200
    assert b"Aarav Sharma" in res_detail.data
    assert b"Change History Audit Trail" in res_detail.data
    assert b"Linked Risk Cases" in res_detail.data


def test_duplicate_student_id_and_apaar_rejected_within_same_institution(client, a7_fixture):
    """Verify duplicate institutional student ID and duplicate APAAR within the same institution are rejected."""
    login(client, "VESIT")

    # 1. Attempt duplicate institution_student_id (V-2026-001 already exists in VESIT)
    dup_id_payload = {
        "institution_student_id": "V-2026-001",
        "full_name": "Different Name",
        "apaar_ref": "987654329999",
        "academic_year": "2026-27",
        "institution_id": a7_fixture["vesit_id"],
        "programme_id": a7_fixture["vesit_it_id"],
        "admission_category": "OPEN",
        "classification": "REGULAR",
        "admission_date": "2026-07-05",
    }
    res1 = client.post("/api/students", json=dup_id_payload)
    assert res1.status_code == 422
    body1 = res1.get_json()
    assert body1["ok"] is False
    codes1 = [e["code"] for e in body1["error"]["details"]["errors"]]
    assert "DUPLICATE_STUDENT_ID" in codes1

    # 2. Attempt duplicate APAAR in the same institution (987654321012 already exists in VESIT)
    dup_apaar_payload = {
        "institution_student_id": "V-2026-999",
        "full_name": "Another Student",
        "apaar_ref": "987654321012",
        "academic_year": "2026-27",
        "institution_id": a7_fixture["vesit_id"],
        "programme_id": a7_fixture["vesit_it_id"],
        "admission_category": "OPEN",
        "classification": "REGULAR",
        "admission_date": "2026-07-05",
    }
    res2 = client.post("/api/students", json=dup_apaar_payload)
    assert res2.status_code == 422
    body2 = res2.get_json()
    assert body2["ok"] is False
    codes2 = [e["code"] for e in body2["error"]["details"]["errors"]]
    assert "DUPLICATE_APAAR_IN_INSTITUTE" in codes2

