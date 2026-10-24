"""
Central Student Data Validation Service for APAAR Nexus.
Task A6 implementation.

Validates student records for manual entry (A7), CSV ingestion (A8), and admissions (A9).
Enforces required fields, APAAR format, academic year rules, date bounds, category constraints,
and duplicate detection.
"""
import re
import unicodedata
from datetime import date, datetime
from typing import Optional, Dict, Any, List, Union
from dataclasses import dataclass, field

from models.institution import Institution, Programme, IntakeCapacity
from models.student import StudentRecord
from models.enums import AdmissionCategory, Classification, EnrolmentStatus
from services.apaar_adapter import validate_format_with_reason, mask_apaar


@dataclass
class ValidationResult:
    """Standardized validation outcome container."""
    valid: bool
    errors: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[Dict[str, Any]] = field(default_factory=list)
    normalized: Dict[str, Any] = field(default_factory=dict)


def parse_bool_flag(value: Any) -> bool:
    """
    Parse boolean flags flexibly from diverse representations:
    True values: True, 1, "1", "true", "yes", "y", "t"
    False values: False, 0, "0", "false", "no", "n", "f", None, ""
    """
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value != 0)
    str_val = str(value).strip().lower()
    return str_val in ("true", "yes", "1", "y", "t")


def normalize_name(value: Optional[str]) -> str:
    """
    Normalize student name:
    - Lowercase
    - Strip accents/combining characters
    - Remove punctuation and symbols (replace with space)
    - Collapse extra whitespace
    """
    if not value:
        return ""
    text = unicodedata.normalize("NFKD", str(value)).casefold()
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join("".join(char if char.isalnum() else " " for char in text).split())


def validate_academic_year(academic_year: Any) -> tuple[bool, str, str]:
    """
    Validate that academic year follows format YYYY-YY and represents consecutive years.
    Example: '2026-27' -> (True, '2026-27')
    """
    if not academic_year:
        return False, "Academic year is required.", ""

    year_str = str(academic_year).strip()
    match = re.fullmatch(r"^(\d{4})-(\d{2})$", year_str)
    if not match:
        return False, "Academic year must follow the YYYY-YY format (e.g. 2026-27).", year_str

    start_year = int(match.group(1))
    end_suffix = int(match.group(2))
    expected_end = (start_year + 1) % 100

    if end_suffix != expected_end:
        return False, f"Academic year must span consecutive years (expected {start_year}-{(start_year + 1) % 100:02d}).", year_str

    if start_year < 2000 or start_year > 2100:
        return False, "Academic year start year must be between 2000 and 2100.", year_str

    return True, "VALID", year_str


def parse_date(date_val: Any) -> tuple[Optional[date], Optional[str]]:
    """Parse date from date object or ISO string. Returns (date, error_message)."""
    if date_val is None or date_val == "":
        return None, "Admission date is required."

    if isinstance(date_val, date) and not isinstance(date_val, datetime):
        parsed = date_val
    elif isinstance(date_val, datetime):
        parsed = date_val.date()
    elif isinstance(date_val, str):
        date_str = date_val.strip()
        try:
            parsed = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            try:
                parsed = datetime.strptime(date_str, "%d-%m-%Y").date()
            except ValueError:
                return None, f"Invalid date format '{date_str}'. Expected YYYY-MM-DD."
    else:
        return None, "Invalid admission date format."

    if parsed > date.today():
        return None, f"Admission date ({parsed.isoformat()}) cannot be in the future."

    if parsed.year < 2000:
        return None, f"Admission date ({parsed.isoformat()}) cannot be prior to the year 2000."

    return parsed, None


class StudentInput:
    """Input structure representing student admission data."""
    def __init__(
        self,
        institution_student_id: str,
        full_name: str,
        institution_id: str,
        programme_id: str,
        academic_year: str,
        admission_date: Union[date, str],
        admission_category: str,
        classification: str,
        apaar_ref: Optional[str] = None,
        is_nri: bool = False,
        is_oci: bool = False,
        is_fn: bool = False,
        enrolment_status: str = "ACTIVE",
        contact_ref: Optional[str] = None,
        fee_payer_ref: Optional[str] = None,
        **kwargs
    ):
        self.institution_student_id = institution_student_id
        self.full_name = full_name
        self.institution_id = institution_id
        self.programme_id = programme_id
        self.academic_year = academic_year
        self.admission_date = admission_date
        self.admission_category = admission_category
        self.classification = classification
        self.apaar_ref = apaar_ref
        self.is_nri = parse_bool_flag(is_nri)
        self.is_oci = parse_bool_flag(is_oci)
        self.is_fn = parse_bool_flag(is_fn)
        self.enrolment_status = enrolment_status
        self.contact_ref = contact_ref
        self.fee_payer_ref = fee_payer_ref

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StudentInput":
        """Build from dictionary supporting alternative key aliases."""
        return cls(
            institution_student_id=data.get("institution_student_id") or data.get("institutional_student_id") or data.get("student_id") or "",
            full_name=data.get("full_name") or data.get("name") or "",
            institution_id=data.get("institution_id") or data.get("institution") or "",
            programme_id=data.get("programme_id") or data.get("programme_code") or data.get("programme") or "",
            academic_year=data.get("academic_year") or data.get("year") or "",
            admission_date=data.get("admission_date") or data.get("date") or "",
            admission_category=data.get("admission_category") or data.get("category") or "",
            classification=data.get("classification") or "",
            apaar_ref=data.get("apaar_ref") or data.get("apaar") or None,
            is_nri=data.get("is_nri") or data.get("nri", False),
            is_oci=data.get("is_oci") or data.get("oci", False),
            is_fn=data.get("is_fn") or data.get("fn", False),
            enrolment_status=data.get("enrolment_status") or "ACTIVE",
            contact_ref=data.get("contact_ref") or data.get("phone") or data.get("contact") or None,
            fee_payer_ref=data.get("fee_payer_ref") or data.get("fee_payer") or None
        )


def validate_student_row(
    row: Union[Dict[str, Any], StudentInput],
    context: Optional[Dict[str, Any]] = None
) -> ValidationResult:
    """
    Validate a student admission record against system rules.
    Reusable by manual entry (A7), CSV ingestion (A8), and admission ledger (A9).

    Args:
        row: StudentInput object or dictionary.
        context: Optional dictionary containing batch context or preloaded models.

    Returns:
        ValidationResult with valid flag, errors list, warnings list, and normalized dictionary.
    """
    if isinstance(row, dict):
        student = StudentInput.from_dict(row)
    else:
        student = row

    context = context or {}
    errors: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []

    def add_error(field_name: str, code: str, message: str):
        errors.append({"field": field_name, "code": code, "message": message})

    def add_warning(field_name: str, code: str, message: str):
        warnings.append({"field": field_name, "code": code, "message": message})

    # 1. Required Fields Check
    student_id = str(student.institution_student_id or "").strip()
    if not student_id:
        add_error("institution_student_id", "MISSING_FIELD", "Institutional student ID is required.")

    name = str(student.full_name or "").strip()
    if not name:
        add_error("full_name", "MISSING_FIELD", "Student full name is required.")

    inst_ref = str(student.institution_id or "").strip()
    if not inst_ref:
        add_error("institution_id", "MISSING_FIELD", "Institution reference is required.")

    prog_ref = str(student.programme_id or "").strip()
    if not prog_ref:
        add_error("programme_id", "MISSING_FIELD", "Programme reference is required.")

    # 2. APAAR Format Check
    apaar = str(student.apaar_ref or "").strip()
    if not apaar:
        add_error("apaar_ref", "MISSING_FIELD", "APAAR reference is required.")
    else:
        is_apaar_valid, apaar_reason = validate_format_with_reason(apaar)
        if not is_apaar_valid:
            add_error("apaar_ref", "APAAR_FORMAT", apaar_reason)

    # 3. Academic Year Check
    year_valid, year_msg, clean_year = validate_academic_year(student.academic_year)
    if not year_valid:
        add_error("academic_year", "BAD_YEAR", year_msg)

    # 4. Admission Date Check
    parsed_date, date_err = parse_date(student.admission_date)
    if date_err:
        add_error("admission_date", "BAD_DATE", date_err)

    # 5. Admission Category & Classification Check
    category_str = str(student.admission_category or "").strip().upper()
    valid_categories = {c.value for c in AdmissionCategory}
    if not category_str or category_str not in valid_categories:
        add_error("admission_category", "BAD_CATEGORY", f"Invalid category '{category_str}'. Must be one of {sorted(list(valid_categories))}.")

    classification_str = str(student.classification or "").strip().upper()
    valid_classifications = {c.value for c in Classification}
    if not classification_str or classification_str not in valid_classifications:
        add_error("classification", "BAD_CATEGORY", f"Invalid classification '{classification_str}'. Must be REGULAR or SUPERNUMERARY.")

    # 6. Institution Resolution
    institution = None
    if inst_ref:
        institution = Institution.query.filter(
            (Institution.id == inst_ref) | (Institution.code == inst_ref.upper())
        ).first()
        if not institution:
            add_error("institution_id", "UNKNOWN_PROGRAMME", f"Institution '{inst_ref}' not found in registry.")

    # 7. Programme Resolution
    programme = None
    if institution and prog_ref:
        programme = Programme.query.filter(
            (Programme.institution_id == institution.id) &
            ((Programme.id == prog_ref) | (Programme.code == prog_ref.upper()))
        ).first()
        if not programme:
            add_error("programme_id", "UNKNOWN_PROGRAMME", f"Programme '{prog_ref}' does not exist for institution '{institution.code}'.")

    # 8. Capacity Row Verification
    capacity = None
    if institution and programme and year_valid:
        capacity = IntakeCapacity.query.filter_by(
            institution_id=institution.id,
            programme_id=programme.id,
            academic_year=clean_year
        ).first()
        if not capacity:
            add_error("programme_id", "UNKNOWN_PROGRAMME", f"No intake capacity configured for {programme.code} in {clean_year}.")

    # 9. Category & Classification Consistency Rules
    is_nri = parse_bool_flag(student.is_nri)
    is_oci = parse_bool_flag(student.is_oci)
    is_fn = parse_bool_flag(student.is_fn)

    if category_str in ("NRI", "OCI", "FN"):
        # Category flag consistency
        if category_str == "NRI" and not is_nri:
            is_nri = True
        elif category_str == "OCI" and not is_oci:
            is_oci = True
        elif category_str == "FN" and not is_fn:
            is_fn = True

        # Check against capacity category rules if defined
        if capacity and capacity.category_rules:
            rules = capacity.category_rules
            allowed_classifications = rules.get(category_str, {}).get("allowed_classifications")
            if allowed_classifications and classification_str not in allowed_classifications:
                add_error(
                    "classification",
                    "CATEGORY_CLASSIFICATION_MISMATCH",
                    f"Category '{category_str}' is restricted to {allowed_classifications} for this programme (got '{classification_str}')."
                )
        else:
            # Standard government rule: International categories (NRI/OCI/FN) are generally supernumerary
            # If regular classification is requested for NRI without explicit allowance, flag mismatch if rules forbid it
            pass

    # 10. Duplicate Checks
    if institution and student_id and clean_year:
        # Check duplicate institutional student ID in the same institution and year
        query_dup_id = StudentRecord.query.filter(
            StudentRecord.institution_id == institution.id,
            StudentRecord.academic_year == clean_year,
            StudentRecord.institution_student_id == student_id
        )
        current_rec_id = context.get("current_student_id")
        if current_rec_id:
            query_dup_id = query_dup_id.filter(StudentRecord.id != current_rec_id)

        if query_dup_id.first():
            add_error(
                "institution_student_id",
                "DUPLICATE_STUDENT_ID",
                f"Student ID '{student_id}' is already enrolled at {institution.code} for academic year {clean_year}."
            )

    if apaar and is_apaar_valid and institution and clean_year:
        # Duplicate APAAR within same institution + year (Hard Error)
        query_dup_apaar = StudentRecord.query.filter(
            StudentRecord.institution_id == institution.id,
            StudentRecord.academic_year == clean_year,
            StudentRecord.apaar_ref == apaar
        )
        if current_rec_id:
            query_dup_apaar = query_dup_apaar.filter(StudentRecord.id != current_rec_id)

        if query_dup_apaar.first():
            add_error(
                "apaar_ref",
                "DUPLICATE_APAAR_IN_INSTITUTE",
                f"APAAR {mask_apaar(apaar)} is already enrolled at {institution.code} in {clean_year}."
            )

        # Duplicate APAAR across different institutions (WARNING only, not rejection!)
        query_cross_apaar = StudentRecord.query.filter(
            StudentRecord.institution_id != institution.id,
            StudentRecord.apaar_ref == apaar,
            StudentRecord.enrolment_status == EnrolmentStatus.ACTIVE.value
        )
        cross_match = query_cross_apaar.first()
        if cross_match:
            add_warning(
                "apaar_ref",
                "CROSS_INSTITUTION_DUPLICATE_WARNING",
                f"APAAR {mask_apaar(apaar)} is currently active at another institution. This record will trigger an identity conflict review."
            )

    # 11. Enrolment Status
    enrolment_status = str(student.enrolment_status or "ACTIVE").strip().upper()
    if enrolment_status not in {s.value for s in EnrolmentStatus}:
        enrolment_status = EnrolmentStatus.ACTIVE.value

    # Build normalized payload
    normalized = {
        "institution_student_id": student_id,
        "full_name": name,
        "normalized_name": normalize_name(name),
        "institution_id": institution.id if institution else inst_ref,
        "programme_id": programme.id if programme else prog_ref,
        "academic_year": clean_year if year_valid else student.academic_year,
        "admission_date": parsed_date.isoformat() if parsed_date else student.admission_date,
        "admission_category": category_str,
        "classification": classification_str,
        "apaar_ref": apaar,
        "apaar_masked": mask_apaar(apaar),
        "is_nri": is_nri,
        "is_oci": is_oci,
        "is_fn": is_fn,
        "enrolment_status": enrolment_status,
        "contact_ref": str(student.contact_ref).strip() if student.contact_ref else None,
        "fee_payer_ref": str(student.fee_payer_ref).strip() if student.fee_payer_ref else None,
    }

    return ValidationResult(
        valid=(len(errors) == 0),
        errors=errors,
        warnings=warnings,
        normalized=normalized
    )
