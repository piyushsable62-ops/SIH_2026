"""
SQLAlchemy models package for APAAR Nexus.
Exports all relational entities, enums, and database initialization helper.
"""
from extensions import db
from models.base import generate_uuid, TimestampMixin
from models.enums import (
    Role,
    Classification,
    AdmissionCategory,
    EnrolmentStatus,
    ValidationStatus,
    SubmissionStatus,
    AdmissionEventResult,
    RiskSeverity,
    CaseStatus,
    CaseActionType,
    CredentialStatus,
    VerificationResult,
    SharingClaim,
    SharingExpiryHours,
)
from models.user import User
from models.institution import Institution, Programme, IntakeCapacity
from models.student import StudentRecord, StudentRecordHistory
from models.admission import AdmissionEvent, DataSubmission, SubmissionError
from models.identity import IdentityMatch, IdentityGraphEdge
from models.risk import RiskCase, RiskEvidence, CaseAction
from models.credential import (
    Credential,
    SigningKey,
    ConsentRecord,
    SharingSession,
    VerificationEvent,
)
from models.audit import AuditLog
from models.policy import SystemPolicy


def init_db(app=None):
    """
    Initialize all database tables.
    Used by app.py on startup and seed.py on database reset.
    """
    if app:
        with app.app_context():
            db.create_all()
    else:
        db.create_all()


__all__ = [
    "db",
    "init_db",
    "generate_uuid",
    "TimestampMixin",
    # Enums
    "Role",
    "Classification",
    "AdmissionCategory",
    "EnrolmentStatus",
    "ValidationStatus",
    "SubmissionStatus",
    "AdmissionEventResult",
    "RiskSeverity",
    "CaseStatus",
    "CaseActionType",
    "CredentialStatus",
    "VerificationResult",
    "SharingClaim",
    "SharingExpiryHours",
    # Models
    "User",
    "Institution",
    "Programme",
    "IntakeCapacity",
    "StudentRecord",
    "StudentRecordHistory",
    "AdmissionEvent",
    "DataSubmission",
    "SubmissionError",
    "IdentityMatch",
    "IdentityGraphEdge",
    "RiskCase",
    "RiskEvidence",
    "CaseAction",
    "Credential",
    "SigningKey",
    "ConsentRecord",
    "SharingSession",
    "VerificationEvent",
    "AuditLog",
    "SystemPolicy",
]
