"""
Audit log model for APAAR Nexus.
Stores append-only, tamper-evident action logs for all mutations.
"""
from datetime import datetime
from extensions import db
from models.base import generate_uuid


class AuditLog(db.Model):
    """Immutable audit trail of all mutating platform actions."""
    __tablename__ = "audit_logs"

    id = db.Column(db.String(36), primary_key=True, default=generate_uuid)
    actor_id = db.Column(db.String(80), nullable=False, index=True)
    actor_role = db.Column(db.String(30), nullable=True)
    action = db.Column(db.String(50), nullable=False, index=True)
    entity_type = db.Column(db.String(50), nullable=False, index=True)
    entity_id = db.Column(db.String(50), nullable=False, index=True)
    institution_id = db.Column(db.String(36), nullable=True, index=True)
    details = db.Column(db.JSON, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "actor_id": self.actor_id,
            "actor_role": self.actor_role,
            "action": self.action,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "institution_id": self.institution_id,
            "details": self.details,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self) -> str:
        return f"<AuditLog {self.actor_id} -> {self.action} on {self.entity_type}:{self.entity_id}>"
