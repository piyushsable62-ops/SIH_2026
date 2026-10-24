"""
CSV Ingestion Service for APAAR Nexus.
Smart India Hackathon PS 26263 — Task A8.

Provides:
- Canonical template definitions & aliases
- Ingestion limits & CSV formula injection sanitization
- preview(file, institution_id): Validates up to MAX_CSV_ROWS, checks idempotency, row-level status
- commit(submission_id, institution_id, update_existing): Atomically persists valid rows, records submission errors, triggers scan hook
- Error report generation & download
"""
import io
import csv
import hashlib
import secrets
from datetime import datetime, date
from typing import Optional, Dict, Any, List, Tuple

from flask import current_app
from extensions import db
from models.institution import Institution, Programme, IntakeCapacity
from models.student import StudentRecord, StudentRecordHistory
from models.admission import DataSubmission, SubmissionError
from models.enums import SubmissionStatus, EnrolmentStatus, ValidationStatus
from services.validation_service import validate_student_row, parse_bool_flag
from services.apaar_adapter import mask_apaar
from services.audit_service import log as audit_log
from services import seat_ledger

# Canonical CSV headers in required order
CANONICAL_HEADERS = [
    "institution_student_id",
    "apaar_ref",
    "full_name",
    "programme_code",
    "academic_year",
    "admission_date",
    "admission_category",
    "classification",
    "is_nri",
    "is_oci",
    "is_foreign_national",
    "enrolment_status",
    "contact_ref",
    "fee_payer_ref"
]

# Required columns that must be present (or aliased)
REQUIRED_COLUMNS = [
    "institution_student_id",
    "apaar_ref",
    "full_name",
    "programme_code",
    "academic_year",
    "admission_date",
    "admission_category",
    "classification"
]

# Documented explicit header alias map
HEADER_ALIASES = {
    "student_id": "institution_student_id",
    "institutional_student_id": "institution_student_id",
    "roll_no": "institution_student_id",
    "registration_no": "institution_student_id",
    "id": "institution_student_id",
    "apaar": "apaar_ref",
    "apaar_id": "apaar_ref",
    "apaar_number": "apaar_ref",
    "name": "full_name",
    "student_name": "full_name",
    "programme": "programme_code",
    "program": "programme_code",
    "degree": "programme_code",
    "year": "academic_year",
    "admission_yr": "academic_year",
    "date_of_admission": "admission_date",
    "category": "admission_category",
    "seat_type": "classification",
    "seat_classification": "classification",
    "nri": "is_nri",
    "oci": "is_oci",
    "foreign_national": "is_foreign_national",
    "fn": "is_foreign_national",
    "status": "enrolment_status",
    "contact": "contact_ref",
    "phone": "contact_ref",
    "email": "contact_ref",
    "fee_payer": "fee_payer_ref",
}

DANGEROUS_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def generate_submission_reference() -> str:
    """Generate human-friendly submission reference per AGENTS.md §5.8 (SUB-YYYYMMDD-XXXXXX)."""
    date_str = datetime.utcnow().strftime("%Y%m%d")
    rand_hex = secrets.token_hex(3).upper()
    return f"SUB-{date_str}-{rand_hex}"


def sanitize_csv_cell_for_export(val: Any) -> str:
    """Prevent CSV formula injection by prepending single quote if starting with formula triggers."""
    s = str(val or "")
    if s.startswith(DANGEROUS_FORMULA_PREFIXES):
        return f"'{s}"
    return s


def check_csv_injection(val: Any) -> Tuple[bool, str]:
    """Detect and neutralize CSV formula injection on input cells."""
    if val is None:
        return False, ""
    s = str(val)
    if not s:
        return False, ""
    if s.startswith(DANGEROUS_FORMULA_PREFIXES):
        # Neutralize by stripping leading trigger
        clean = s.lstrip("=+-@\t\r ").strip()
        return True, clean
    return False, s.strip()


def generate_csv_template() -> str:
    """Generate canonical CSV template content with documented headers and 2 sample rows."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(CANONICAL_HEADERS)
    writer.writerow([
        "VESIT-26-BTECH-IT-0201",
        "980000000001",
        "Manan Joshi",
        "IT",
        "2026-27",
        "2026-07-20",
        "OPEN",
        "REGULAR",
        "false",
        "false",
        "false",
        "ACTIVE",
        "manan@example.com",
        "FP-TXN-001"
    ])
    writer.writerow([
        "VESIT-26-BTECH-IT-0202",
        "980000000002",
        "Sanya Verma",
        "IT",
        "2026-27",
        "2026-07-20",
        "NRI",
        "SUPERNUMERARY",
        "true",
        "false",
        "false",
        "ACTIVE",
        "+91 9876543210",
        "FP-TXN-002"
    ])
    return output.getvalue()


def _trigger_identity_scan(institution_id: Optional[str] = None, submission_id: Optional[str] = None):
    """Guarded trigger for Member B's identity resolution scan hook."""
    try:
        from services.identity_resolution import scan
        scan_res = scan(institution_id=institution_id, submission_id=submission_id)
        try:
            from services.risk_engine import evaluate_all
            evaluate_all(scan_res)
        except Exception as risk_exc:
            current_app.logger.warning("Risk engine hook failed after CSV commit: %s", risk_exc)
            audit_log(
                actor="SYSTEM",
                action="SCAN_RISK_ERROR",
                entity_type="SYSTEM",
                entity_id=submission_id or institution_id or "all",
                details={"error": str(risk_exc)},
                institution_id=institution_id
            )
    except Exception as exc:
        current_app.logger.warning("Identity scan hook failed after CSV commit: %s", exc)
        audit_log(
            actor="SYSTEM",
            action="IDENTITY_SCAN_ERROR",
            entity_type="SYSTEM",
            entity_id=submission_id or institution_id or "all",
            details={"error": str(exc)},
            institution_id=institution_id
        )


# ---------------------------------------------------------------------------
# Core Preview Logic
# ---------------------------------------------------------------------------

def preview_csv(
    file_bytes: bytes,
    institution_id: str,
    filename: str = "upload.csv",
    uploaded_by: str = "SYSTEM"
) -> Dict[str, Any]:
    """
    Parse up to MAX_CSV_ROWS, validate every row with validation_service,
    check idempotency, and return summary + first 50 rows.
    Creates a DataSubmission record with status PREVIEWED.
    Does NOT insert student records.
    """
    institution = db.session.get(Institution, institution_id)
    if not institution:
        raise ValueError("Invalid institution ID.")

    # 1. Enforce file limits
    max_mb = current_app.config.get("MAX_UPLOAD_MB", 2)
    max_bytes = max_mb * 1024 * 1024
    if len(file_bytes) > max_bytes:
        raise ValueError(f"File size exceeds maximum allowed limit of {max_mb} MB.")

    if not filename.lower().endswith(".csv"):
        raise ValueError("Only .csv files are supported.")

    # Compute SHA-256
    file_sha256 = hashlib.sha256(file_bytes).hexdigest()

    # 2. Idempotency Check: Was this exact file already committed for this institution?
    prior_committed = DataSubmission.query.filter(
        DataSubmission.institution_id == institution_id,
        DataSubmission.file_sha256 == file_sha256,
        DataSubmission.status.in_([SubmissionStatus.COMMITTED.value, SubmissionStatus.PARTIAL.value])
    ).first()

    if prior_committed:
        # Record identity signal for duplicate CSV submission
        audit_log(
            actor=uploaded_by,
            action="DUPLICATE_SUBMISSION",
            entity_type="DATA_SUBMISSION",
            entity_id=prior_committed.id,
            details={
                "file_sha256": file_sha256,
                "filename": filename,
                "original_reference": prior_committed.reference
            },
            institution_id=institution_id
        )
        return {
            "already_imported": True,
            "idempotent": True,
            "reference": prior_committed.reference,
            "submission_id": prior_committed.id,
            "message": "This file has already been imported for this institution.",
            "status": prior_committed.status,
            "total_rows": prior_committed.total_rows,
            "valid_rows": prior_committed.valid_rows,
            "error_rows": prior_committed.error_rows
        }

    # 3. Decode UTF-8 with BOM tolerance
    try:
        text_content = file_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError("CSV file must be UTF-8 encoded.")

    stream = io.StringIO(text_content, newline="")
    reader = csv.reader(stream)

    try:
        raw_headers = next(reader)
    except StopIteration:
        raise ValueError("The uploaded CSV file is empty.")

    # Normalize headers with aliases
    normalized_headers = []
    for h in raw_headers:
        clean_h = h.strip().lower().replace(" ", "_")
        canonical_h = HEADER_ALIASES.get(clean_h, clean_h)
        normalized_headers.append(canonical_h)

    # Verify all required columns are present
    missing_required = [col for col in REQUIRED_COLUMNS if col not in normalized_headers]
    if missing_required:
        raise ValueError(f"Missing required CSV columns: {', '.join(missing_required)}")

    max_rows = current_app.config.get("MAX_CSV_ROWS", 5000)

    parsed_rows = []
    seen_ids_in_file = set()
    seen_apaar_in_file = set()

    total_count = 0
    valid_count = 0
    error_count = 0
    warning_count = 0
    unchanged_count = 0
    update_candidate_count = 0

    # Cache programmes for this institution to optimize row lookups
    programmes = {p.code.upper(): p for p in Programme.query.filter_by(institution_id=institution.id).all()}
    programmes.update({p.id: p for p in programmes.values()})

    for row_idx, raw_vals in enumerate(reader, start=2):
        if row_idx - 1 > max_rows:
            break
        total_count += 1

        # Map to dict
        row_dict = {}
        row_injection_detected = False

        for h_name, val in zip(normalized_headers, raw_vals):
            has_injection, clean_val = check_csv_injection(val)
            if has_injection:
                row_injection_detected = True
            row_dict[h_name] = clean_val

        # Add institution context
        row_dict["institution_id"] = institution.id

        # Resolve programme code -> programme_id if needed
        prog_val = str(row_dict.get("programme_code") or "").strip().upper()
        if prog_val in programmes:
            row_dict["programme_id"] = programmes[prog_val].id

        st_id = str(row_dict.get("institution_student_id") or "").strip()
        apaar = str(row_dict.get("apaar_ref") or "").strip()
        year = str(row_dict.get("academic_year") or "").strip()

        row_errors = []
        row_warnings = []

        if row_injection_detected:
            row_warnings.append({
                "field": "general",
                "code": "CSV_INJECTION_NEUTRALIZED",
                "message": "Potential formula injection character neutralized in cell."
            })

        # 4. In-file duplicate checks
        id_year_key = (st_id, year)
        if st_id and year:
            if id_year_key in seen_ids_in_file:
                row_errors.append({
                    "field": "institution_student_id",
                    "code": "DUPLICATE_STUDENT_ID_IN_FILE",
                    "message": f"Duplicate student ID '{st_id}' for academic year '{year}' within this file."
                })
            else:
                seen_ids_in_file.add(id_year_key)

        if apaar and len(apaar) == 12 and apaar.isdigit():
            if apaar in seen_apaar_in_file:
                row_errors.append({
                    "field": "apaar_ref",
                    "code": "DUPLICATE_APAAR_IN_FILE",
                    "message": f"Duplicate APAAR reference '{mask_apaar(apaar)}' within this file."
                })
            else:
                seen_apaar_in_file.add(apaar)

        # Check existing record in DB for update / idempotency checks
        existing_rec = None
        if st_id and year:
            existing_rec = StudentRecord.query.filter_by(
                institution_id=institution.id,
                institution_student_id=st_id,
                academic_year=year
            ).first()

        # 5. Central validation service validation
        val_ctx = {"current_student_id": existing_rec.id} if existing_rec else None
        val_res = validate_student_row(row_dict, context=val_ctx)
        if not val_res.valid:
            row_errors.extend(val_res.errors)
        if val_res.warnings:
            row_warnings.extend(val_res.warnings)

        # 6. Row-level idempotency and existing record check
        row_status = "VALID"
        if row_errors:
            row_status = "INVALID"
            error_count += 1
        elif existing_rec:
            # Compare fields for identical match
            is_identical = (
                existing_rec.full_name == val_res.normalized.get("full_name") and
                existing_rec.apaar_ref == val_res.normalized.get("apaar_ref") and
                existing_rec.programme_id == val_res.normalized.get("programme_id") and
                existing_rec.admission_category == val_res.normalized.get("admission_category") and
                existing_rec.classification == val_res.normalized.get("classification") and
                existing_rec.is_nri == val_res.normalized.get("is_nri") and
                existing_rec.is_oci == val_res.normalized.get("is_oci") and
                existing_rec.is_foreign_national == val_res.normalized.get("is_fn")
            )

            if is_identical:
                row_status = "UNCHANGED"
                unchanged_count += 1
            else:
                row_status = "UPDATE_CANDIDATE"
                update_candidate_count += 1
        else:
            valid_count += 1

        if row_warnings:
            warning_count += 1

        parsed_rows.append({
            "row_number": row_idx,
            "status": row_status,
            "raw_row": row_dict,
            "normalized": val_res.normalized,
            "errors": row_errors,
            "warnings": row_warnings
        })

    # 7. Create or update preview DataSubmission record
    # Look for existing PREVIEWED record with this hash, or create new
    submission = DataSubmission.query.filter_by(
        institution_id=institution.id,
        file_sha256=file_sha256,
        status=SubmissionStatus.PREVIEWED.value
    ).first()

    if not submission:
        ref = generate_submission_reference()
        submission = DataSubmission(
            reference=ref,
            institution_id=institution.id,
            uploaded_by=uploaded_by,
            filename=filename,
            file_sha256=file_sha256,
            status=SubmissionStatus.PREVIEWED.value,
            total_rows=total_count,
            valid_rows=valid_count + update_candidate_count,
            error_rows=error_count,
            details={
                "parsed_rows": parsed_rows,
                "summary": {
                    "total_rows": total_count,
                    "valid_rows": valid_count,
                    "error_rows": error_count,
                    "warning_rows": warning_count,
                    "unchanged_rows": unchanged_count,
                    "update_candidate_rows": update_candidate_count
                }
            }
        )
        db.session.add(submission)
    else:
        submission.total_rows = total_count
        submission.valid_rows = valid_count + update_candidate_count
        submission.error_rows = error_count
        submission.details = {
            "parsed_rows": parsed_rows,
            "summary": {
                "total_rows": total_count,
                "valid_rows": valid_count,
                "error_rows": error_count,
                "warning_rows": warning_count,
                "unchanged_rows": unchanged_count,
                "update_candidate_rows": update_candidate_count
            }
        }

    db.session.commit()

    return {
        "already_imported": False,
        "idempotent": False,
        "submission_id": submission.id,
        "reference": submission.reference,
        "status": submission.status,
        "summary": {
            "total_rows": total_count,
            "valid_rows": valid_count,
            "error_rows": error_count,
            "warning_rows": warning_count,
            "unchanged_rows": unchanged_count,
            "update_candidate_rows": update_candidate_count
        },
        # Return first 50 rows for wizard preview display
        "preview_rows": parsed_rows[:50]
    }


# ---------------------------------------------------------------------------
# Core Commit Logic
# ---------------------------------------------------------------------------

def commit_submission(
    submission_id: str,
    institution_id: str,
    update_existing: bool = False,
    committed_by: str = "SYSTEM"
) -> Dict[str, Any]:
    """
    In ONE transaction:
    - Inserts all VALID rows into student_records
    - Updates UPDATE_CANDIDATE rows if update_existing=True (writing history)
    - Records SubmissionError rows for invalid rows
    - Updates counts and sets status COMMITTED, PARTIAL, or REJECTED
    - Logs audit event
    - Triggers identity resolution scan hook
    """
    submission = db.session.get(DataSubmission, submission_id)
    if not submission:
        raise ValueError("Data submission not found.")

    if submission.institution_id != institution_id:
        raise PermissionError("Cross-institution submission commit forbidden.")

    if submission.status in (SubmissionStatus.COMMITTED.value, SubmissionStatus.PARTIAL.value):
        return {
            "already_committed": True,
            "reference": submission.reference,
            "status": submission.status,
            "message": "Submission is already committed.",
            "valid_rows": submission.valid_rows,
            "error_rows": submission.error_rows
        }

    rows = (submission.details or {}).get("parsed_rows", [])
    if not rows:
        raise ValueError("No parsed rows found in submission preview.")

    inserted_count = 0
    updated_count = 0
    unchanged_count = 0
    error_count = 0

    try:
        for r in rows:
            row_num = r["row_number"]
            status = r["status"]
            raw_row = r.get("raw_row", {})
            norm = r.get("normalized", {})
            errs = r.get("errors", [])
            warnings = r.get("warnings", [])

            if status == "INVALID" or errs:
                error_count += 1
                for err in errs:
                    db.session.add(SubmissionError(
                        submission_id=submission.id,
                        row_number=row_num,
                        field=err.get("field"),
                        code=err.get("code", "VALIDATION_ERROR"),
                        message=err.get("message", "Validation failed."),
                        raw_row=raw_row
                    ))
                continue

            if status == "UNCHANGED":
                unchanged_count += 1
                continue

            if status == "UPDATE_CANDIDATE":
                if not update_existing:
                    # Treat as error / rejection without overwrite
                    error_count += 1
                    db.session.add(SubmissionError(
                        submission_id=submission.id,
                        row_number=row_num,
                        field="institution_student_id",
                        code="EXISTING_RECORD_DIFFERS",
                        message=f"Student ID '{norm.get('institution_student_id')}' already exists with different data. Re-upload with update_existing=true to update.",
                        raw_row=raw_row
                    ))
                    continue

                # Update existing student record with history
                existing = StudentRecord.query.filter_by(
                    institution_id=submission.institution_id,
                    institution_student_id=norm.get("institution_student_id"),
                    academic_year=norm.get("academic_year")
                ).first()

                if existing:
                    updated_fields = []
                    for f in ["full_name", "apaar_ref", "programme_id", "admission_category", "classification", "is_nri", "is_oci", "contact_ref", "fee_payer_ref"]:
                        new_v = norm.get(f)
                        old_v = getattr(existing, f)
                        if str(new_v or "") != str(old_v or ""):
                            db.session.add(StudentRecordHistory(
                                student_id=existing.id,
                                changed_by=committed_by,
                                field=f,
                                old_value=str(old_v) if old_v is not None else "",
                                new_value=str(new_v) if new_v is not None else "",
                                reason=f"CSV import update via {submission.reference}"
                            ))
                            setattr(existing, f, new_v)
                            updated_fields.append(f)

                    if "full_name" in updated_fields:
                        existing.normalized_name = norm.get("normalized_name")

                    existing.source_submission_id = submission.id
                    if warnings:
                        existing.validation_status = ValidationStatus.FLAGGED.value
                        existing.validation_notes = warnings[0]["message"]
                    else:
                        existing.validation_status = ValidationStatus.VALID.value
                        existing.validation_notes = None

                    updated_count += 1
                continue

            adm_date = norm.get("admission_date")
            if isinstance(adm_date, str):
                try:
                    adm_date = date.fromisoformat(adm_date)
                except ValueError:
                    adm_date = date.today()

            admission_payload = {
                "institution_id": submission.institution_id,
                "programme_id": norm["programme_id"],
                "institution_student_id": norm["institution_student_id"],
                "apaar_ref": norm.get("apaar_ref"),
                "full_name": norm["full_name"],
                "academic_year": norm["academic_year"],
                "admission_date": adm_date,
                "admission_category": norm["admission_category"],
                "classification": norm["classification"],
                "is_nri": norm.get("is_nri", False),
                "is_oci": norm.get("is_oci", False),
                "is_foreign_national": norm.get("is_fn", False),
                "enrolment_status": norm.get("enrolment_status", EnrolmentStatus.ACTIVE.value),
                "validation_status": ValidationStatus.FLAGGED.value if warnings else ValidationStatus.VALID.value,
                "validation_notes": warnings[0]["message"] if warnings else None,
                "contact_ref": norm.get("contact_ref"),
                "fee_payer_ref": norm.get("fee_payer_ref"),
                "source_submission_id": submission.id,
            }

            try:
                seat_ledger.commit_admission(admission_payload, actor=submission.uploaded_by)
                inserted_count += 1
            except seat_ledger.SeatLedgerError as exc:
                sub_err = SubmissionError(
                    submission_id=submission.id,
                    row_number=row_num,
                    field_name="intake_capacity",
                    error_code=exc.reason_code,
                    message=exc.message,
                    raw_row_json=json.dumps(row_dict)
                )
                db.session.add(sub_err)
                error_count += 1

        # Determine final submission status
        valid_total = inserted_count + updated_count
        if error_count == 0:
            final_status = SubmissionStatus.COMMITTED.value
        elif valid_total > 0:
            final_status = SubmissionStatus.PARTIAL.value
        else:
            final_status = SubmissionStatus.REJECTED.value

        submission.status = final_status
        submission.valid_rows = valid_total
        submission.error_rows = error_count
        submission.committed_at = datetime.utcnow()

        db.session.commit()

        # Audit log the committed submission
        audit_log(
            actor=committed_by,
            action="CSV_IMPORT_COMMITTED",
            entity_type="DATA_SUBMISSION",
            entity_id=submission.id,
            details={
                "reference": submission.reference,
                "status": final_status,
                "inserted_count": inserted_count,
                "updated_count": updated_count,
                "unchanged_count": unchanged_count,
                "error_count": error_count,
                "total_rows": submission.total_rows
            },
            institution_id=submission.institution_id
        )

        # Trigger identity resolution scan hook
        _trigger_identity_scan(institution_id=submission.institution_id, submission_id=submission.id)

        return {
            "ok": True,
            "submission_id": submission.id,
            "reference": submission.reference,
            "status": final_status,
            "inserted_count": inserted_count,
            "updated_count": updated_count,
            "unchanged_count": unchanged_count,
            "error_count": error_count,
            "total_rows": submission.total_rows
        }

    except Exception as exc:
        db.session.rollback()
        current_app.logger.error("Failed committing CSV submission %s: %s", submission_id, exc)
        raise exc


# ---------------------------------------------------------------------------
# Error Report Generation Logic
# ---------------------------------------------------------------------------

def generate_error_report_csv(submission_id: str, institution_id: Optional[str] = None) -> Tuple[str, str]:
    """
    Generate CSV error report containing row numbers, field, code, message,
    and raw original values. Neutralizes formula injection on cell export.
    Returns (csv_string, filename).
    """
    submission = db.session.get(DataSubmission, submission_id)
    if not submission:
        raise ValueError("Data submission not found.")

    if institution_id and submission.institution_id != institution_id:
        raise PermissionError("Access to another institution's error report is forbidden.")

    errors = SubmissionError.query.filter_by(submission_id=submission.id).order_by(SubmissionError.row_number).all()

    output = io.StringIO()
    writer = csv.writer(output)

    # Header
    writer.writerow([
        "Row Number",
        "Field",
        "Error Code",
        "Error Message",
        "Student ID (Raw)",
        "APAAR Ref (Raw)",
        "Full Name (Raw)",
        "Programme Code (Raw)",
        "Academic Year (Raw)",
        "Admission Category (Raw)"
    ])

    for err in errors:
        raw = err.raw_row or {}
        writer.writerow([
            err.row_number,
            sanitize_csv_cell_for_export(err.field or "General"),
            sanitize_csv_cell_for_export(err.code),
            sanitize_csv_cell_for_export(err.message),
            sanitize_csv_cell_for_export(raw.get("institution_student_id", "")),
            sanitize_csv_cell_for_export(mask_apaar(raw.get("apaar_ref", ""))),
            sanitize_csv_cell_for_export(raw.get("full_name", "")),
            sanitize_csv_cell_for_export(raw.get("programme_code", "")),
            sanitize_csv_cell_for_export(raw.get("academic_year", "")),
            sanitize_csv_cell_for_export(raw.get("admission_category", ""))
        ])

    filename = f"error_report_{submission.reference}_{datetime.utcnow().strftime('%Y%m%d')}.csv"
    return output.getvalue(), filename
