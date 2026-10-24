"""
Tests for APAAR adapter and central validation service (Task A6).
Tests format validation, masking, deterministic mock verification, name normalization,
and all validation error/warning codes.
"""
import pytest
from datetime import date, timedelta
from extensions import db
from models.institution import Institution, Programme, IntakeCapacity
from models.student import StudentRecord
from models.enums import AdmissionCategory, Classification, EnrolmentStatus, ValidationStatus
from services.apaar_adapter import validate_format, mask_apaar, mock_verify
from services.validation_service import (
    validate_student_row,
    normalize_name,
    parse_bool_flag,
    validate_academic_year,
    StudentInput,
    ValidationResult
)


@pytest.fixture
def validation_fixture(app):
    """Seed test institutions, programmes, and capacities for validation tests."""
    with app.app_context():
        # Cleanup
        db.session.query(StudentRecord).delete()
        db.session.query(IntakeCapacity).delete()
        db.session.query(Programme).delete()
        db.session.query(Institution).delete()
        db.session.commit()

        # Institution 1: VESIT
        vesit = Institution(code="VESIT", name="VESIT Mumbai", state="Maharashtra", city="Mumbai")
        # Institution 2: COEP
        coep = Institution(code="COEP", name="COEP Pune", state="Maharashtra", city="Pune")
        db.session.add_all([vesit, coep])
        db.session.flush()

        # Programme: IT in VESIT, CS in COEP
        it_prog = Programme(institution_id=vesit.id, code="IT", name="Information Technology")
        cs_prog = Programme(institution_id=coep.id, code="CS", name="Computer Science")
        db.session.add_all([it_prog, cs_prog])
        db.session.flush()

        # Capacities: 2026-27
        vesit_cap = IntakeCapacity(
            institution_id=vesit.id,
            programme_id=it_prog.id,
            academic_year="2026-27",
            approved_regular_intake=120,
            supernumerary_capacity=10,
            category_rules={
                "NRI": {"allowed_classifications": ["SUPERNUMERARY"]},
                "FN": {"allowed_classifications": ["SUPERNUMERARY"]}
            }
        )
        coep_cap = IntakeCapacity(
            institution_id=coep.id,
            programme_id=cs_prog.id,
            academic_year="2026-27",
            approved_regular_intake=120,
            supernumerary_capacity=10
        )
        db.session.add_all([vesit_cap, coep_cap])
        db.session.flush()

        # Existing student in VESIT: V001 with APAAR 111122223333
        existing_vesit = StudentRecord(
            institution_id=vesit.id,
            programme_id=it_prog.id,
            institution_student_id="V001",
            full_name="Existing Vesit Student",
            normalized_name="existing vesit student",
            academic_year="2026-27",
            admission_date=date.today(),
            admission_category=AdmissionCategory.OPEN.value,
            classification=Classification.REGULAR.value,
            apaar_ref="111122223333",
            enrolment_status=EnrolmentStatus.ACTIVE.value,
            validation_status=ValidationStatus.VALID.value
        )

        # Existing student in COEP: C001 with APAAR 555566667777 (for cross-institution test)
        existing_coep = StudentRecord(
            institution_id=coep.id,
            programme_id=cs_prog.id,
            institution_student_id="C001",
            full_name="Existing Coep Student",
            normalized_name="existing coep student",
            academic_year="2026-27",
            admission_date=date.today(),
            admission_category=AdmissionCategory.OPEN.value,
            classification=Classification.REGULAR.value,
            apaar_ref="555566667777",
            enrolment_status=EnrolmentStatus.ACTIVE.value,
            validation_status=ValidationStatus.VALID.value
        )

        db.session.add_all([existing_vesit, existing_coep])
        db.session.commit()

        return {
            "vesit_id": vesit.id,
            "coep_id": coep.id,
            "it_prog_id": it_prog.id,
            "cs_prog_id": cs_prog.id
        }


# ---------------------------------------------------------------------------
# APAAR Adapter Tests
# ---------------------------------------------------------------------------

def test_mask_apaar():
    """Verify APAAR masking contract: XXXXXXXX9012 (last 4 only)."""
    assert mask_apaar("123456789012") == "XXXXXXXX9012"
    assert mask_apaar("987654321098") == "XXXXXXXX1098"
    assert mask_apaar("") == ""
    assert mask_apaar(None) == ""
    assert mask_apaar("123") == "XXXXXXXX"
    assert mask_apaar("1234") == "XXXXXXXX"


def test_validate_format():
    """Verify format check rules: 12 numeric digits required."""
    # Valid
    assert validate_format("123456789012") is True
    assert validate_format("999988887777") is True

    # Invalid: letters, symbols, wrong length
    assert validate_format("12345678901A") is False
    assert validate_format("12345678901") is False  # 11 digits
    assert validate_format("1234567890123") is False  # 13 digits
    assert validate_format("") is False
    assert validate_format(None) is False

    # Tuple return when unpacking
    valid, reason = validate_format("12345")
    assert valid is False
    assert "12 digits" in reason


def test_mock_verify_deterministic_rules():
    """Verify mock APAAR verification deterministic rules and notice."""
    # Starts with '9' -> MOCK_VERIFIED
    v1 = mock_verify("987654321012")
    assert v1["status"] == "MOCK_VERIFIED"
    assert "MOCK" in v1["source"]
    assert v1["apaar_ref"] == "XXXXXXXX1012"

    # Ends with '0000' -> MOCK_NOT_FOUND
    v2 = mock_verify("123456780000")
    assert v2["status"] == "MOCK_NOT_FOUND"

    # Invalid format -> MOCK_NOT_FOUND
    v3 = mock_verify("INVALID")
    assert v3["status"] == "MOCK_NOT_FOUND"

    # Other valid format -> MOCK_UNAVAILABLE
    v4 = mock_verify("123456789012")
    assert v4["status"] == "MOCK_UNAVAILABLE"


# ---------------------------------------------------------------------------
# Normalization & Helpers Tests
# ---------------------------------------------------------------------------

def test_normalize_name():
    """Verify name normalization: lowercase, strip accents, collapse spaces."""
    assert normalize_name("  RÓHAN--Deshmukh ") == "rohan deshmukh"
    assert normalize_name("Dr. A. B. Sharma") == "dr a b sharma"
    assert normalize_name("O'Connor") == "o connor"
    assert normalize_name("") == ""
    assert normalize_name(None) == ""


def test_parse_bool_flag():
    """Verify boolean flag parsing for NRI/OCI/FN flags."""
    for true_val in [True, 1, "1", "true", "True", "YES", "yes", "Y", "t"]:
        assert parse_bool_flag(true_val) is True

    for false_val in [False, 0, "0", "false", "NO", "no", "n", "", None]:
        assert parse_bool_flag(false_val) is False


def test_validate_academic_year():
    """Verify academic year YYYY-YY consecutive years format."""
    ok, _, yr = validate_academic_year("2026-27")
    assert ok is True
    assert yr == "2026-27"

    # Non-consecutive
    bad1, msg1, _ = validate_academic_year("2026-28")
    assert bad1 is False
    assert "consecutive" in msg1

    # Bad format
    bad2, _, _ = validate_academic_year("2026-2027")
    assert bad2 is False


# ---------------------------------------------------------------------------
# Central Student Row Validation Tests
# ---------------------------------------------------------------------------

def test_valid_student_row(app, validation_fixture):
    """Verify successful validation on a clean student row."""
    with app.app_context():
        row = {
            "institution_student_id": "V099",
            "full_name": "Aarav Sharma",
            "institution_id": validation_fixture["vesit_id"],
            "programme_id": validation_fixture["it_prog_id"],
            "academic_year": "2026-27",
            "admission_date": date.today(),
            "admission_category": "OPEN",
            "classification": "REGULAR",
            "apaar_ref": "987654321012",
        }
        res = validate_student_row(row)
        assert res.valid is True
        assert len(res.errors) == 0
        assert res.normalized["normalized_name"] == "aarav sharma"
        assert res.normalized["apaar_masked"] == "XXXXXXXX1012"


def test_missing_field_errors(app, validation_fixture):
    """Verify MISSING_FIELD error code on omitted mandatory values."""
    with app.app_context():
        res = validate_student_row({})
        assert res.valid is False
        codes = [e["code"] for e in res.errors]
        assert "MISSING_FIELD" in codes


def test_apaar_format_error(app, validation_fixture):
    """Verify APAAR_FORMAT error code on malformed APAAR."""
    with app.app_context():
        row = {
            "institution_student_id": "V099",
            "full_name": "Aarav Sharma",
            "institution_id": validation_fixture["vesit_id"],
            "programme_id": validation_fixture["it_prog_id"],
            "academic_year": "2026-27",
            "admission_date": date.today(),
            "admission_category": "OPEN",
            "classification": "REGULAR",
            "apaar_ref": "12345",  # Too short
        }
        res = validate_student_row(row)
        assert res.valid is False
        codes = [e["code"] for e in res.errors]
        assert "APAAR_FORMAT" in codes


def test_bad_category_error(app, validation_fixture):
    """Verify BAD_CATEGORY error code on invalid category string."""
    with app.app_context():
        row = {
            "institution_student_id": "V099",
            "full_name": "Aarav Sharma",
            "institution_id": validation_fixture["vesit_id"],
            "programme_id": validation_fixture["it_prog_id"],
            "academic_year": "2026-27",
            "admission_date": date.today(),
            "admission_category": "INVALID_CAT",
            "classification": "REGULAR",
            "apaar_ref": "987654321012",
        }
        res = validate_student_row(row)
        assert res.valid is False
        codes = [e["code"] for e in res.errors]
        assert "BAD_CATEGORY" in codes


def test_unknown_programme_error(app, validation_fixture):
    """Verify UNKNOWN_PROGRAMME error code when programme code doesn't exist."""
    with app.app_context():
        row = {
            "institution_student_id": "V099",
            "full_name": "Aarav Sharma",
            "institution_id": validation_fixture["vesit_id"],
            "programme_id": "NON_EXISTENT_PROG",
            "academic_year": "2026-27",
            "admission_date": date.today(),
            "admission_category": "OPEN",
            "classification": "REGULAR",
            "apaar_ref": "987654321012",
        }
        res = validate_student_row(row)
        assert res.valid is False
        codes = [e["code"] for e in res.errors]
        assert "UNKNOWN_PROGRAMME" in codes


def test_bad_date_error(app, validation_fixture):
    """Verify BAD_DATE error code on future dates or pre-2000 dates."""
    with app.app_context():
        future_date = date.today() + timedelta(days=10)
        row = {
            "institution_student_id": "V099",
            "full_name": "Aarav Sharma",
            "institution_id": validation_fixture["vesit_id"],
            "programme_id": validation_fixture["it_prog_id"],
            "academic_year": "2026-27",
            "admission_date": future_date,
            "admission_category": "OPEN",
            "classification": "REGULAR",
            "apaar_ref": "987654321012",
        }
        res = validate_student_row(row)
        assert res.valid is False
        codes = [e["code"] for e in res.errors]
        assert "BAD_DATE" in codes


def test_bad_year_error(app, validation_fixture):
    """Verify BAD_YEAR error code on non-consecutive academic year."""
    with app.app_context():
        row = {
            "institution_student_id": "V099",
            "full_name": "Aarav Sharma",
            "institution_id": validation_fixture["vesit_id"],
            "programme_id": validation_fixture["it_prog_id"],
            "academic_year": "2026-29",
            "admission_date": date.today(),
            "admission_category": "OPEN",
            "classification": "REGULAR",
            "apaar_ref": "987654321012",
        }
        res = validate_student_row(row)
        assert res.valid is False
        codes = [e["code"] for e in res.errors]
        assert "BAD_YEAR" in codes


def test_duplicate_student_id_error(app, validation_fixture):
    """Verify DUPLICATE_STUDENT_ID error code within same institution and year."""
    with app.app_context():
        # V001 already exists in VESIT for 2026-27
        row = {
            "institution_student_id": "V001",
            "full_name": "Another Student",
            "institution_id": validation_fixture["vesit_id"],
            "programme_id": validation_fixture["it_prog_id"],
            "academic_year": "2026-27",
            "admission_date": date.today(),
            "admission_category": "OPEN",
            "classification": "REGULAR",
            "apaar_ref": "987654321012",
        }
        res = validate_student_row(row)
        assert res.valid is False
        codes = [e["code"] for e in res.errors]
        assert "DUPLICATE_STUDENT_ID" in codes


def test_duplicate_apaar_in_institute_error(app, validation_fixture):
    """Verify DUPLICATE_APAAR_IN_INSTITUTE error code within same institution and year."""
    with app.app_context():
        # 111122223333 already belongs to V001 in VESIT
        row = {
            "institution_student_id": "V002",
            "full_name": "Another Student",
            "institution_id": validation_fixture["vesit_id"],
            "programme_id": validation_fixture["it_prog_id"],
            "academic_year": "2026-27",
            "admission_date": date.today(),
            "admission_category": "OPEN",
            "classification": "REGULAR",
            "apaar_ref": "111122223333",
        }
        res = validate_student_row(row)
        assert res.valid is False
        codes = [e["code"] for e in res.errors]
        assert "DUPLICATE_APAAR_IN_INSTITUTE" in codes


def test_cross_institution_duplicate_produces_warning_not_rejection(app, validation_fixture):
    """
    CRITICAL CONTRACT: Duplicate APAAR across DIFFERENT institutions must produce
    a WARNING for identity resolution review, NOT a hard validation rejection!
    """
    with app.app_context():
        # 555566667777 belongs to student C001 in COEP
        # Now enrolling into VESIT:
        row = {
            "institution_student_id": "V002",
            "full_name": "Dual Enrolment Student",
            "institution_id": validation_fixture["vesit_id"],
            "programme_id": validation_fixture["it_prog_id"],
            "academic_year": "2026-27",
            "admission_date": date.today(),
            "admission_category": "OPEN",
            "classification": "REGULAR",
            "apaar_ref": "555566667777",
        }
        res = validate_student_row(row)
        # Must be valid so it passes into ingestion & identity scan!
        assert res.valid is True
        assert len(res.errors) == 0

        # Warning must be attached
        assert len(res.warnings) == 1
        assert res.warnings[0]["code"] == "CROSS_INSTITUTION_DUPLICATE_WARNING"


def test_category_classification_mismatch(app, validation_fixture):
    """Verify CATEGORY_CLASSIFICATION_MISMATCH when NRI regular admission is forbidden by capacity rules."""
    with app.app_context():
        # In fixture, category_rules specifies: NRI allowed_classifications: ["SUPERNUMERARY"]
        row = {
            "institution_student_id": "V099",
            "full_name": "Aarav Sharma",
            "institution_id": validation_fixture["vesit_id"],
            "programme_id": validation_fixture["it_prog_id"],
            "academic_year": "2026-27",
            "admission_date": date.today(),
            "admission_category": "NRI",
            "classification": "REGULAR",  # Forbidden by category_rules
            "apaar_ref": "987654321012",
        }
        res = validate_student_row(row)
        assert res.valid is False
        codes = [e["code"] for e in res.errors]
        assert "CATEGORY_CLASSIFICATION_MISMATCH" in codes
