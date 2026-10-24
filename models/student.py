"""
Student records and student change history models for APAAR Nexus.
"""
from datetime import datetime, date
from extensions import db
from models.base import generate_uuid, TimestampMixin
from models.enums import Classification, AdmissionCategory, EnrolmentStatus, ValidationStatus


class StudentRecord(TimestampMixin, db.Model):
    """Student academic record indexed by external APAAR identifier."""
    __tablename__ = "student_records"
    __table_args__ = (
        db.UniqueConstraint("institution_id", "institution_student_id", "academic_year", name="uq_student_inst_reg_year"),
        db.Index("ix_student_apaar_ref", "apaar_ref"),
        db.Index("ix_student_normalized_name", "normalized_name"),
        db.Index("ix_student_contact_ref", "contact_ref"),
        db.Index("ix_student_fee_payer_ref", "fee_payer_ref"),
    )

    id = db.Column(db.String(36), primary_key=True, default=generate_uuid)
    institution_id = db.Column(db.String(36), db.ForeignKey("institutions.id", ondelete="CASCADE"), nullable=False, index=True)
    programme_id = db.Column(db.String(36), db.ForeignKey("programmes.id", ondelete="CASCADE"), nullable=False, index=True)
    institution_student_id = db.Column(db.String(50), nullable=False)
    apaar_ref = db.Column(db.String(20), nullable=True)  # Indexed, NOT unique per AGENTS.md §5.4
    full_name = db.Column(db.String(150), nullable=False)
    normalized_name = db.Column(db.String(150), nullable=False)
    academic_year = db.Column(db.String(20), nullable=False, index=True)
    admission_date = db.Column(db.Date, nullable=False, default=date.today)
    admission_category = db.Column(db.String(30), nullable=False, default=AdmissionCategory.OPEN.value)
    classification = db.Column(db.String(30), nullable=False, default=Classification.REGULAR.value)

    # Specific demographic flags
    is_nri = db.Column(db.Boolean, default=False, nullable=False)
    is_oci = db.Column(db.Boolean, default=False, nullable=False)
    is_foreign_national = db.Column(db.Boolean, default=False, nullable=False)

    # Status tracking
    enrolment_status = db.Column(db.String(30), nullable=False, default=EnrolmentStatus.ACTIVE.value, index=True)
    validation_status = db.Column(db.String(30), nullable=False, default=ValidationStatus.PENDING.value, index=True)
    validation_notes = db.Column(db.Text, nullable=True)

    # Investigation signals (deliberately NOT unique)
    contact_ref = db.Column(db.String(100), nullable=True)
    fee_payer_ref = db.Column(db.String(100), nullable=True)

    # Ingestion tracking
    source_submission_id = db.Column(db.String(36), db.ForeignKey("data_submissions.id", ondelete="SET NULL"), nullable=True, index=True)
    is_synthetic = db.Column(db.Boolean, default=True, nullable=False)

    # Relationships
    institution = db.relationship("Institution", backref=db.backref("students", lazy="dynamic"))
    programme = db.relationship("Programme", backref=db.backref("students", lazy="dynamic"))
    history = db.relationship("StudentRecordHistory", backref="student", cascade="all, delete-orphan", lazy="dynamic")
    credentials = db.relationship("Credential", backref="student", cascade="all, delete-orphan", lazy="dynamic")

    def to_dict(self, mask_apaar: bool = True) -> dict:
        apaar = self.apaar_ref
        if apaar and mask_apaar:
            apaar = f"XXXXXXXX{apaar[-4:]}" if len(apaar) >= 4 else "XXXXXXXX"

        return {
            "id": self.id,
            "institution_id": self.institution_id,
            "programme_id": self.programme_id,
            "institution_student_id": self.institution_student_id,
            "apaar_ref": apaar,
            "full_name": self.full_name,
            "normalized_name": self.normalized_name,
            "academic_year": self.academic_year,
            "admission_date": self.admission_date.isoformat() if self.admission_date else None,
            "admission_category": self.admission_category,
            "classification": self.classification,
            "is_nri": self.is_nri,
            "is_oci": self.is_oci,
            "is_foreign_national": self.is_foreign_national,
            "enrolment_status": self.enrolment_status,
            "validation_status": self.validation_status,
            "validation_notes": self.validation_notes,
            "is_synthetic": self.is_synthetic,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self) -> str:
        return f"<StudentRecord {self.institution_student_id}: {self.full_name}>"


class StudentRecordHistory(db.Model):
    """Audit log of individual field changes made to student records."""
    __tablename__ = "student_record_history"

    id = db.Column(db.String(36), primary_key=True, default=generate_uuid)
    student_id = db.Column(db.String(36), db.ForeignKey("student_records.id", ondelete="CASCADE"), nullable=False, index=True)
    changed_by = db.Column(db.String(80), nullable=False)
    changed_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    field = db.Column(db.String(50), nullable=False)
    old_value = db.Column(db.Text, nullable=True)
    new_value = db.Column(db.Text, nullable=True)
    reason = db.Column(db.String(255), nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "student_id": self.student_id,
            "changed_by": self.changed_by,
            "changed_at": self.changed_at.isoformat() if self.changed_at else None,
            "field": self.field,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "reason": self.reason,
        }

    def __repr__(self) -> str:
        return f"<StudentRecordHistory {self.field} on {self.student_id}>"
