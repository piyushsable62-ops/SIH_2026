"""
Academic Passport Credentials, Signing Keys, Consent, and Sharing models for APAAR Nexus.
Used by Member B's Ed25519 Credential & Privacy-Preserving Sharing services.
"""
from datetime import datetime, date
from extensions import db
from models.base import generate_uuid, TimestampMixin
from models.enums import CredentialStatus, VerificationResult


class Credential(TimestampMixin, db.Model):
    """Cryptographically signed academic credential (Ed25519)."""
    __tablename__ = "credentials"

    id = db.Column(db.String(36), primary_key=True, default=generate_uuid)
    reference = db.Column(db.String(30), unique=True, nullable=False, index=True)  # CRED-XXXXXX
    student_id = db.Column(db.String(36), db.ForeignKey("student_records.id", ondelete="CASCADE"), nullable=False, index=True)
    institution_id = db.Column(db.String(36), db.ForeignKey("institutions.id", ondelete="CASCADE"), nullable=False, index=True)
    programme_name = db.Column(db.String(255), nullable=False)
    award_year = db.Column(db.String(20), nullable=False)
    issue_date = db.Column(db.Date, nullable=False, default=date.today)
    expires_at = db.Column(db.DateTime, nullable=True)

    # Cryptographic integrity
    payload_json = db.Column(db.Text, nullable=False)  # Canonical JSON text that was signed
    signature = db.Column(db.Text, nullable=False)     # Base64 encoded signature
    key_id = db.Column(db.String(50), nullable=False, index=True)

    # Revocation state
    status_stored = db.Column(db.String(30), nullable=False, default=CredentialStatus.ISSUED.value, index=True)
    revoked_at = db.Column(db.DateTime, nullable=True)
    revoked_by = db.Column(db.String(80), nullable=True)
    revoke_reason = db.Column(db.Text, nullable=True)

    # Relationships
    institution = db.relationship("Institution", backref=db.backref("credentials", lazy="dynamic"))
    sharing_sessions = db.relationship("SharingSession", backref="credential", cascade="all, delete-orphan", lazy="dynamic")
    verifications = db.relationship("VerificationEvent", backref="credential", cascade="all, delete-orphan", lazy="dynamic")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "reference": self.reference,
            "student_id": self.student_id,
            "institution_id": self.institution_id,
            "programme_name": self.programme_name,
            "award_year": self.award_year,
            "issue_date": self.issue_date.isoformat() if self.issue_date else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "key_id": self.key_id,
            "status_stored": self.status_stored,
            "revoked_at": self.revoked_at.isoformat() if self.revoked_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self) -> str:
        return f"<Credential {self.reference} [{self.status_stored}]>"


class SigningKey(db.Model):
    """Public key metadata for Ed25519 signing keys. Private keys NEVER stored here."""
    __tablename__ = "signing_keys"

    id = db.Column(db.String(36), primary_key=True, default=generate_uuid)
    key_id = db.Column(db.String(50), unique=True, nullable=False, index=True)
    public_key_pem = db.Column(db.Text, nullable=False)
    algorithm = db.Column(db.String(30), nullable=False, default="Ed25519")
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    is_development_key = db.Column(db.Boolean, default=True, nullable=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "key_id": self.key_id,
            "public_key_pem": self.public_key_pem,
            "algorithm": self.algorithm,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "is_active": self.is_active,
            "is_development_key": self.is_development_key,
        }

    def __repr__(self) -> str:
        return f"<SigningKey {self.key_id} [{self.algorithm}]>"


class ConsentRecord(db.Model):
    """Student consent grant for claim sharing."""
    __tablename__ = "consent_records"

    id = db.Column(db.String(36), primary_key=True, default=generate_uuid)
    student_id = db.Column(db.String(36), db.ForeignKey("student_records.id", ondelete="CASCADE"), nullable=False, index=True)
    claims = db.Column(db.JSON, nullable=False)  # List of allowed SharingClaim strings
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    student = db.relationship("StudentRecord", backref=db.backref("consents", lazy="dynamic"))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "student_id": self.student_id,
            "claims": self.claims,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self) -> str:
        return f"<ConsentRecord {self.id} for Student:{self.student_id}>"


class SharingSession(db.Model):
    """Time-limited, student-controlled credential sharing session."""
    __tablename__ = "sharing_sessions"

    id = db.Column(db.String(36), primary_key=True, default=generate_uuid)
    student_id = db.Column(db.String(36), db.ForeignKey("student_records.id", ondelete="CASCADE"), nullable=False, index=True)
    credential_id = db.Column(db.String(36), db.ForeignKey("credentials.id", ondelete="CASCADE"), nullable=False, index=True)
    consent_id = db.Column(db.String(36), db.ForeignKey("consent_records.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash = db.Column(db.String(64), unique=True, nullable=False, index=True)  # SHA-256 hash of token
    token_prefix = db.Column(db.String(10), nullable=False)  # First 6 chars only for identification
    claims = db.Column(db.JSON, nullable=False)              # Selective claims exposed
    expires_at = db.Column(db.DateTime, nullable=False, index=True)
    revoked_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    access_count = db.Column(db.Integer, default=0, nullable=False)
    last_accessed_at = db.Column(db.DateTime, nullable=True)

    # Relationships
    student = db.relationship("StudentRecord", backref=db.backref("sharing_sessions", lazy="dynamic"))
    consent = db.relationship("ConsentRecord", backref=db.backref("sessions", lazy="dynamic"))
    verifications = db.relationship("VerificationEvent", backref="sharing_session", cascade="all, delete-orphan", lazy="dynamic")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "student_id": self.student_id,
            "credential_id": self.credential_id,
            "token_prefix": self.token_prefix,
            "claims": self.claims,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "revoked_at": self.revoked_at.isoformat() if self.revoked_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "access_count": self.access_count,
            "last_accessed_at": self.last_accessed_at.isoformat() if self.last_accessed_at else None,
        }

    def __repr__(self) -> str:
        return f"<SharingSession {self.token_prefix}... Exp:{self.expires_at}>"


class VerificationEvent(db.Model):
    """Log of QR or shared-link credential verification attempt."""
    __tablename__ = "verification_events"

    id = db.Column(db.String(36), primary_key=True, default=generate_uuid)
    credential_id = db.Column(db.String(36), db.ForeignKey("credentials.id", ondelete="SET NULL"), nullable=True, index=True)
    sharing_session_id = db.Column(db.String(36), db.ForeignKey("sharing_sessions.id", ondelete="SET NULL"), nullable=True, index=True)
    result = db.Column(db.String(30), nullable=False, default=VerificationResult.VERIFIED.value, index=True)
    verifier_user_id = db.Column(db.String(36), nullable=True)
    ip_hash = db.Column(db.String(64), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "credential_id": self.credential_id,
            "sharing_session_id": self.sharing_session_id,
            "result": self.result,
            "verifier_user_id": self.verifier_user_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self) -> str:
        return f"<VerificationEvent {self.result} Cred:{self.credential_id}>"
