"""
System Policies model for APAAR Nexus.
Stores dynamic configuration such as risk weights and APAAR regex rules.
"""
from datetime import datetime
from extensions import db
from models.base import generate_uuid


class SystemPolicy(db.Model):
    """Configurable system policies, risk weights, and validation rules."""
    __tablename__ = "system_policies"

    id = db.Column(db.String(36), primary_key=True, default=generate_uuid)
    key = db.Column(db.String(80), unique=True, nullable=False, index=True)
    value = db.Column(db.JSON, nullable=False)
    description = db.Column(db.Text, nullable=True)
    updated_by = db.Column(db.String(80), nullable=False, default="SYSTEM")
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "key": self.key,
            "value": self.value,
            "description": self.description,
            "updated_by": self.updated_by,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self) -> str:
        return f"<SystemPolicy {self.key}>"
