"""
Admission Events and Data Ingestion models for APAAR Nexus.
"""
from datetime import datetime
from extensions import db
from models.base import generate_uuid, TimestampMixin
from models.enums import AdmissionEventResult, SubmissionStatus, Classification, AdmissionCategory


class AdmissionEvent(db.Model):
    """Immutable ledger of all admission attempts (both ACCEPTED and REJECTED)."""
    __tablename__ = "admission_events"

    id = db.Column(db.String(36), primary_key=True, default=generate_uuid)
    institution_id = db.Column(db.String(36), db.ForeignKey("institutions.id", ondelete="CASCADE"), nullable=False, index=True)
    programme_id = db.Column(db.String(36), db.ForeignKey("programmes.id", ondelete="CASCADE"), nullable=False, index=True)
    academic_year = db.Column(db.String(20), nullable=False, index=True)
    student_id = db.Column(db.String(36), db.ForeignKey("student_records.id", ondelete="SET NULL"), nullable=True, index=True)
    category = db.Column(db.String(30), nullable=False, default=AdmissionCategory.OPEN.value)
    classification = db.Column(db.String(30), nullable=False, default=Classification.REGULAR.value)
    result = db.Column(db.String(30), nullable=False, default=AdmissionEventResult.ACCEPTED.value, index=True)
    reason_code = db.Column(db.String(50), nullable=False, default="APPROVED")
    message = db.Column(db.Text, nullable=True)
    actor_id = db.Column(db.String(80), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

    # Relationships
    institution = db.relationship("Institution", backref=db.backref("admission_events", lazy="dynamic"))
    programme = db.relationship("Programme", backref=db.backref("admission_events", lazy="dynamic"))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "institution_id": self.institution_id,
            "programme_id": self.programme_id,
            "academic_year": self.academic_year,
            "student_id": self.student_id,
            "category": self.category,
            "classification": self.classification,
            "result": self.result,
            "reason_code": self.reason_code,
            "message": self.message,
            "actor_id": self.actor_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self) -> str:
        return f"<AdmissionEvent {self.result} [{self.reason_code}] Inst:{self.institution_id}>"


class DataSubmission(db.Model):
    """Batch CSV data submission metadata with idempotent hash tracking."""
    __tablename__ = "data_submissions"
    __table_args__ = (
        db.UniqueConstraint("institution_id", "file_sha256", name="uq_submission_inst_hash"),
    )

    id = db.Column(db.String(36), primary_key=True, default=generate_uuid)
    reference = db.Column(db.String(50), unique=True, nullable=False, index=True)  # SUB-YYYYMMDD-XXXXXX
    institution_id = db.Column(db.String(36), db.ForeignKey("institutions.id", ondelete="CASCADE"), nullable=False, index=True)
    uploaded_by = db.Column(db.String(80), nullable=False)
    filename = db.Column(db.String(255), nullable=False)
    file_sha256 = db.Column(db.String(64), nullable=False, index=True)
    status = db.Column(db.String(30), nullable=False, default=SubmissionStatus.PREVIEWED.value, index=True)
    total_rows = db.Column(db.Integer, nullable=False, default=0)
    valid_rows = db.Column(db.Integer, nullable=False, default=0)
    error_rows = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    committed_at = db.Column(db.DateTime, nullable=True)
    details = db.Column(db.JSON, nullable=True)

    # Relationships
    institution = db.relationship("Institution", backref=db.backref("submissions", lazy="dynamic"))
    errors = db.relationship("SubmissionError", backref="submission", cascade="all, delete-orphan", lazy="dynamic")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "reference": self.reference,
            "institution_id": self.institution_id,
            "uploaded_by": self.uploaded_by,
            "filename": self.filename,
            "file_sha256": self.file_sha256,
            "status": self.status,
            "total_rows": self.total_rows,
            "valid_rows": self.valid_rows,
            "error_rows": self.error_rows,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "committed_at": self.committed_at.isoformat() if self.committed_at else None,
            "details": self.details,
        }

    def __repr__(self) -> str:
        return f"<DataSubmission {self.reference} [{self.status}]>"


class SubmissionError(db.Model):
    """Row-level validation error from CSV processing."""
    __tablename__ = "submission_errors"

    id = db.Column(db.String(36), primary_key=True, default=generate_uuid)
    submission_id = db.Column(db.String(36), db.ForeignKey("data_submissions.id", ondelete="CASCADE"), nullable=False, index=True)
    row_number = db.Column(db.Integer, nullable=False)
    field = db.Column(db.String(50), nullable=True)
    code = db.Column(db.String(50), nullable=False)
    message = db.Column(db.Text, nullable=False)
    raw_row = db.Column(db.JSON, nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "submission_id": self.submission_id,
            "row_number": self.row_number,
            "field": self.field,
            "code": self.code,
            "message": self.message,
            "raw_row": self.raw_row,
        }

    def __repr__(self) -> str:
        return f"<SubmissionError row={self.row_number} code={self.code}>"
