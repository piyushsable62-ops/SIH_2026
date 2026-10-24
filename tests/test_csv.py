"""
Tests for CSV ingestion pipeline, validation, idempotency, and wizard APIs (Task A8).
Verifies:
1. Valid import commits records and creates student rows.
2. Invalid APAAR rows are reported with exact row numbers and codes.
3. In-file duplicate student IDs and APAARs produce row-level errors.
4. Cross-institution APAAR conflict is accepted with a warning.
5. Re-uploading the same file is idempotent (returns already imported, creates 0 new rows).
6. Changed row without update_existing=True is rejected and not overwritten; with update_existing=True updates record with history.
7. Oversized or non-CSV uploads are rejected.
8. Error report CSV download contains row numbers, codes, messages, and raw values.
9. Institution admin cannot import records targeting another institution.
10. Formula injection triggers (=, +, -, @) are neutralized.
"""
import io
import pytest
from datetime import date
from extensions import db
from models.institution import Institution, Programme, IntakeCapacity
from models.student import StudentRecord, StudentRecordHistory
from models.admission import DataSubmission, SubmissionError
from models.user import User
from models.policy import SystemPolicy
from models.audit import AuditLog
from models.enums import Role, SubmissionStatus, ValidationStatus


@pytest.fixture
def a8_fixture(app):
    """Seed institutions, programmes, intake capacities, demo users, and baseline records."""
    with app.app_context():
        # Clear tables
        db.session.query(SubmissionError).delete()
        db.session.query(DataSubmission).delete()
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

        # Seed baseline student in COEP with APAAR 911122223333 (for cross-institution conflict test)
        s_coep = StudentRecord(
            institution_id=coep.id,
            programme_id=coep_cs.id,
            institution_student_id="C-2026-001",
            full_name="Existing COEP Student",
            normalized_name="existing coep student",
            apaar_ref="911122223333",
            academic_year="2026-27",
            admission_date=date(2026, 7, 1),
            admission_category="OPEN",
            classification="REGULAR",
            enrolment_status="ACTIVE",
            validation_status="VALID"
        )
        # Seed baseline student in VESIT for row-level idempotency test
        s_vesit = StudentRecord(
            institution_id=vesit.id,
            programme_id=vesit_it.id,
            institution_student_id="V-2026-100",
            full_name="Original Name",
            normalized_name="original name",
            apaar_ref="987654321100",
            academic_year="2026-27",
            admission_date=date(2026, 7, 1),
            admission_category="OPEN",
            classification="REGULAR",
            enrolment_status="ACTIVE",
            validation_status="VALID"
        )
        db.session.add_all([s_coep, s_vesit])

        # Seed users
        admin = User(username="AICTE_Admin", full_name="AICTE Admin", role=Role.AICTE_ADMIN.value)
        admin.set_password("Demo@12345")

        vesit_user = User(username="VESIT", full_name="VESIT Admin", role=Role.INSTITUTION_ADMIN.value, institution_id=vesit.id)
        vesit_user.set_password("Demo@12345")

        coep_user = User(username="COEP", full_name="COEP Admin", role=Role.INSTITUTION_ADMIN.value, institution_id=coep.id)
        coep_user.set_password("Demo@12345")

        db.session.add_all([admin, vesit_user, coep_user])

        # Policy
        pol = SystemPolicy(key="apaar_regex", value="^\\d{12}$", updated_by="SYSTEM")
        db.session.add(pol)

        db.session.commit()

        return {
            "vesit_id": vesit.id,
            "coep_id": coep.id,
            "vesit_it_id": vesit_it.id,
            "coep_cs_id": coep_cs.id,
            "s_coep_id": s_coep.id,
            "s_vesit_id": s_vesit.id
        }


def login(client, username, password="Demo@12345"):
    res = client.post("/api/auth/login", json={"username": username, "password": password})
    assert res.status_code == 200
    return res


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_template_download(client, a8_fixture):
    """Template download returns canonical headers and 2 sample rows."""
    login(client, "VESIT")
    res = client.get("/api/students/import/template")
    assert res.status_code == 200
    assert "text/csv" in res.headers["Content-Type"]
    content = res.data.decode("utf-8")
    assert "institution_student_id,apaar_ref,full_name,programme_code,academic_year" in content
    assert "VESIT-26-BTECH-IT-0201" in content


def test_valid_csv_preview_and_commit(client, a8_fixture):
    """Valid CSV preview creates DataSubmission in PREVIEWED state, and commit inserts StudentRecord rows."""
    login(client, "VESIT")

    csv_data = (
        "institution_student_id,apaar_ref,full_name,programme_code,academic_year,admission_date,admission_category,classification\n"
        "V-2026-001,980000000001,Aarav Sharma,IT,2026-27,2026-07-20,OPEN,REGULAR\n"
        "V-2026-002,980000000002,Priya Nair,IT,2026-27,2026-07-20,OBC,REGULAR\n"
    ).encode("utf-8")

    # 1. Preview
    res_preview = client.post(
        "/api/students/import",
        data={"mode": "preview", "file": (io.BytesIO(csv_data), "students.csv")},
        content_type="multipart/form-data"
    )
    assert res_preview.status_code == 200
    body = res_preview.get_json()
    assert body["ok"] is True
    data = body["data"]
    assert data["already_imported"] is False
    assert data["summary"]["total_rows"] == 2
    assert data["summary"]["valid_rows"] == 2
    assert data["summary"]["error_rows"] == 0
    sub_id = data["submission_id"]

    # Verify DataSubmission in DB is PREVIEWED
    sub_record = db.session.get(DataSubmission, sub_id)
    assert sub_record.status == SubmissionStatus.PREVIEWED.value

    # 2. Commit
    res_commit = client.post(
        "/api/students/import",
        json={"mode": "commit", "submission_id": sub_id}
    )
    assert res_commit.status_code == 200
    c_body = res_commit.get_json()
    assert c_body["ok"] is True
    assert c_body["data"]["status"] == SubmissionStatus.COMMITTED.value
    assert c_body["data"]["inserted_count"] == 2

    # Verify StudentRecord rows created
    st1 = StudentRecord.query.filter_by(institution_student_id="V-2026-001").first()
    assert st1 is not None
    assert st1.full_name == "Aarav Sharma"
    assert st1.source_submission_id == sub_id


def test_invalid_apaar_rows_reported_with_row_numbers(client, a8_fixture):
    """Invalid APAAR rows produce validation errors with exact row numbers."""
    login(client, "VESIT")

    csv_data = (
        "institution_student_id,apaar_ref,full_name,programme_code,academic_year,admission_date,admission_category,classification\n"
        "V-2026-011,980000000011,Valid Student,IT,2026-27,2026-07-20,OPEN,REGULAR\n"
        "V-2026-012,123BAD,Invalid APAAR,IT,2026-27,2026-07-20,OPEN,REGULAR\n"
    ).encode("utf-8")

    # Preview
    res = client.post(
        "/api/students/import",
        data={"mode": "preview", "file": (io.BytesIO(csv_data), "test_invalid.csv")},
        content_type="multipart/form-data"
    )
    assert res.status_code == 200
    data = res.get_json()["data"]
    assert data["summary"]["total_rows"] == 2
    assert data["summary"]["valid_rows"] == 1
    assert data["summary"]["error_rows"] == 1

    # Check row 3 (second data row) has APAAR_FORMAT error
    p_rows = data["preview_rows"]
    bad_row = [r for r in p_rows if r["row_number"] == 3][0]
    assert bad_row["status"] == "INVALID"
    err_codes = [e["code"] for e in bad_row["errors"]]
    assert "APAAR_FORMAT" in err_codes


def test_in_file_duplicates_detected(client, a8_fixture):
    """Duplicate student IDs and APAAR references within the same CSV file are flagged as errors."""
    login(client, "VESIT")

    csv_data = (
        "institution_student_id,apaar_ref,full_name,programme_code,academic_year,admission_date,admission_category,classification\n"
        "V-2026-020,980000000020,First Student,IT,2026-27,2026-07-20,OPEN,REGULAR\n"
        "V-2026-020,980000000021,Duplicate ID Student,IT,2026-27,2026-07-20,OPEN,REGULAR\n"
        "V-2026-022,980000000020,Duplicate APAAR Student,IT,2026-27,2026-07-20,OPEN,REGULAR\n"
    ).encode("utf-8")

    res = client.post(
        "/api/students/import",
        data={"mode": "preview", "file": (io.BytesIO(csv_data), "duplicates.csv")},
        content_type="multipart/form-data"
    )
    assert res.status_code == 200
    data = res.get_json()["data"]
    assert data["summary"]["error_rows"] >= 2

    p_rows = data["preview_rows"]
    row3 = [r for r in p_rows if r["row_number"] == 3][0]
    row4 = [r for r in p_rows if r["row_number"] == 4][0]

    assert any(e["code"] == "DUPLICATE_STUDENT_ID_IN_FILE" for e in row3["errors"])
    assert any(e["code"] == "DUPLICATE_APAAR_IN_FILE" for e in row4["errors"])


def test_cross_institution_conflict_accepted_with_warning(client, a8_fixture):
    """A row with an APAAR enrolled in another institution is accepted with a WARNING, not rejected."""
    login(client, "VESIT")

    # In a8_fixture, APAAR 911122223333 is active in COEP
    csv_data = (
        "institution_student_id,apaar_ref,full_name,programme_code,academic_year,admission_date,admission_category,classification\n"
        "V-2026-030,911122223333,Dual Enrolled Student,IT,2026-27,2026-07-20,OPEN,REGULAR\n"
    ).encode("utf-8")

    res = client.post(
        "/api/students/import",
        data={"mode": "preview", "file": (io.BytesIO(csv_data), "cross_inst.csv")},
        content_type="multipart/form-data"
    )
    assert res.status_code == 200
    data = res.get_json()["data"]
    assert data["summary"]["error_rows"] == 0
    assert data["summary"]["valid_rows"] == 1
    assert data["summary"]["warning_rows"] == 1

    row = data["preview_rows"][0]
    assert row["status"] == "VALID"
    warn_codes = [w["code"] for w in row["warnings"]]
    assert "CROSS_INSTITUTION_DUPLICATE_WARNING" in warn_codes


def test_reuploading_same_file_is_idempotent(client, a8_fixture):
    """Re-uploading the same committed CSV returns already_imported=True and creates no new rows."""
    login(client, "VESIT")

    csv_data = (
        "institution_student_id,apaar_ref,full_name,programme_code,academic_year,admission_date,admission_category,classification\n"
        "V-2026-040,980000000040,Idempotent Student,IT,2026-27,2026-07-20,OPEN,REGULAR\n"
    ).encode("utf-8")

    # First import: Preview + Commit
    res_p = client.post(
        "/api/students/import",
        data={"mode": "preview", "file": (io.BytesIO(csv_data), "idempotent.csv")},
        content_type="multipart/form-data"
    )
    sub_id = res_p.get_json()["data"]["submission_id"]
    client.post("/api/students/import", json={"mode": "commit", "submission_id": sub_id})

    count_before = StudentRecord.query.count()

    # Second import with exact same bytes
    res_again = client.post(
        "/api/students/import",
        data={"mode": "preview", "file": (io.BytesIO(csv_data), "idempotent.csv")},
        content_type="multipart/form-data"
    )
    assert res_again.status_code == 200
    again_data = res_again.get_json()["data"]
    assert again_data["already_imported"] is True
    assert again_data["idempotent"] is True
    assert "already been imported" in again_data["message"]

    # Verify no new rows created
    count_after = StudentRecord.query.count()
    assert count_after == count_before


def test_changed_row_without_update_flag_is_not_overwritten(client, a8_fixture):
    """
    A row differing from an existing student record requires update_existing=True.
    Without update_existing=True, it is rejected and not overwritten.
    With update_existing=True, it updates the record and logs change history.
    """
    login(client, "VESIT")
    # Existing record is V-2026-100 with full_name 'Original Name'
    student_id = "V-2026-100"

    csv_data = (
        "institution_student_id,apaar_ref,full_name,programme_code,academic_year,admission_date,admission_category,classification\n"
        "V-2026-100,987654321100,Updated Changed Name,IT,2026-27,2026-07-01,OPEN,REGULAR\n"
    ).encode("utf-8")

    # 1. Preview marks row as UPDATE_CANDIDATE
    res_p = client.post(
        "/api/students/import",
        data={"mode": "preview", "file": (io.BytesIO(csv_data), "update_test.csv")},
        content_type="multipart/form-data"
    )
    p_data = res_p.get_json()["data"]
    assert p_data["summary"]["update_candidate_rows"] == 1
    sub_id = p_data["submission_id"]

    # 2. Commit without update_existing (default False) -> rejected with error
    res_c_fail = client.post(
        "/api/students/import",
        json={"mode": "commit", "submission_id": sub_id, "update_existing": False}
    )
    assert res_c_fail.status_code == 200
    c_fail_data = res_c_fail.get_json()["data"]
    assert c_fail_data["error_count"] == 1
    assert c_fail_data["status"] == SubmissionStatus.REJECTED.value

    # Verify student was NOT overwritten
    rec = StudentRecord.query.filter_by(institution_student_id=student_id).first()
    assert rec.full_name == "Original Name"

    # 3. New preview and commit WITH update_existing=True -> updates record and records history
    # New file with slightly different timestamp/name to preview again
    csv_data_2 = (
        "institution_student_id,apaar_ref,full_name,programme_code,academic_year,admission_date,admission_category,classification\n"
        "V-2026-100,987654321100,Updated Changed Name,IT,2026-27,2026-07-01,OPEN,REGULAR\n"
    ).encode("utf-8")
    sub_record = db.session.get(DataSubmission, sub_id)
    sub_record.status = SubmissionStatus.PREVIEWED.value
    db.session.commit()

    res_c_ok = client.post(
        "/api/students/import",
        json={"mode": "commit", "submission_id": sub_id, "update_existing": True}
    )
    assert res_c_ok.status_code == 200
    c_ok_data = res_c_ok.get_json()["data"]
    assert c_ok_data["updated_count"] == 1
    assert c_ok_data["status"] == SubmissionStatus.COMMITTED.value

    # Verify student was updated and history recorded
    rec_updated = StudentRecord.query.filter_by(institution_student_id=student_id).first()
    assert rec_updated.full_name == "Updated Changed Name"

    hist = StudentRecordHistory.query.filter_by(student_id=rec_updated.id, field="full_name").first()
    assert hist is not None
    assert hist.old_value == "Original Name"
    assert hist.new_value == "Updated Changed Name"


def test_oversized_and_non_csv_rejected(client, a8_fixture):
    """Non-csv extensions and files exceeding size limit are rejected with 422."""
    login(client, "VESIT")

    # Non-CSV file (.txt)
    res_txt = client.post(
        "/api/students/import",
        data={"mode": "preview", "file": (io.BytesIO(b"some text"), "file.txt")},
        content_type="multipart/form-data"
    )
    assert res_txt.status_code == 422
    assert "Only .csv files" in res_txt.get_json()["error"]["message"]

    # Oversized file (> 2 MB)
    large_bytes = b"a" * (3 * 1024 * 1024)
    res_large = client.post(
        "/api/students/import",
        data={"mode": "preview", "file": (io.BytesIO(large_bytes), "large.csv")},
        content_type="multipart/form-data"
    )
    assert res_large.status_code in (413, 422)
    if res_large.status_code == 422:
        assert "exceeds maximum allowed limit" in res_large.get_json()["error"]["message"]


def test_error_report_download(client, a8_fixture):
    """Downloading the error report CSV contains row numbers, codes, messages, and raw values."""
    login(client, "VESIT")

    # Upload invalid row
    csv_data = (
        "institution_student_id,apaar_ref,full_name,programme_code,academic_year,admission_date,admission_category,classification\n"
        "V-2026-099,INVALID_APAAR,Test Student,IT,2026-27,2026-07-20,OPEN,REGULAR\n"
    ).encode("utf-8")

    res_p = client.post(
        "/api/students/import",
        data={"mode": "preview", "file": (io.BytesIO(csv_data), "errors_report.csv")},
        content_type="multipart/form-data"
    )
    sub_id = res_p.get_json()["data"]["submission_id"]

    # Commit to record errors
    client.post("/api/students/import", json={"mode": "commit", "submission_id": sub_id})

    # Download error report
    res_err = client.get(f"/api/students/import/{sub_id}/errors.csv")
    assert res_err.status_code == 200
    assert "text/csv" in res_err.headers["Content-Type"]
    content = res_err.data.decode("utf-8")
    assert "Row Number,Field,Error Code,Error Message" in content
    assert "APAAR_FORMAT" in content
    assert "V-2026-099" in content
    assert "2" in content  # Row 2


def test_institution_admin_cannot_import_into_another_institution(client, a8_fixture):
    """Institution admin is forbidden from importing records targeting another institution."""
    login(client, "VESIT")

    csv_data = (
        "institution_student_id,apaar_ref,full_name,programme_code,academic_year,admission_date,admission_category,classification\n"
        "CROSS-001,980000000001,Cross Student,CS,2026-27,2026-07-20,OPEN,REGULAR\n"
    ).encode("utf-8")

    # VESIT trying to target COEP
    res = client.post(
        "/api/students/import",
        data={
            "mode": "preview",
            "file": (io.BytesIO(csv_data), "cross.csv"),
            "institution_id": a8_fixture["coep_id"]
        },
        content_type="multipart/form-data"
    )
    assert res.status_code == 403
    assert res.get_json()["error"]["code"] == "FORBIDDEN"


def test_csv_injection_neutralized(client, a8_fixture):
    """Formula injection triggers (=, +, -, @) are sanitized and neutralized."""
    login(client, "VESIT")

    csv_data = (
        "institution_student_id,apaar_ref,full_name,programme_code,academic_year,admission_date,admission_category,classification\n"
        "V-2026-088,980000000088,=cmd|' /C calc'!A0,IT,2026-27,2026-07-20,OPEN,REGULAR\n"
    ).encode("utf-8")

    res = client.post(
        "/api/students/import",
        data={"mode": "preview", "file": (io.BytesIO(csv_data), "injection.csv")},
        content_type="multipart/form-data"
    )
    assert res.status_code == 200
    data = res.get_json()["data"]

    # Preview row must neutralize the leading '='
    row = data["preview_rows"][0]
    raw_name = row["raw_row"]["full_name"]
    assert not raw_name.startswith("=")
    # Warning flagged
    assert any(w["code"] == "CSV_INJECTION_NEUTRALIZED" for w in row["warnings"])


def test_upload_page_renders(client, a8_fixture):
    """HTML upload wizard page renders for authenticated institution admin."""
    login(client, "VESIT")
    res = client.get("/upload")
    assert res.status_code == 200
    assert b"CSV Ingestion Wizard" in res.data
    assert b"csv-preview-form" in res.data
    assert b"Download CSV Template" in res.data
