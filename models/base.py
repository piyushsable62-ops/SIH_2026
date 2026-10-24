"""
Base model utilities, mixins, and helpers for APAAR Nexus.
"""
import uuid
from datetime import datetime
from extensions import db


def generate_uuid() -> str:
    """Generate a standard UUID4 string primary key."""
    return str(uuid.uuid4())


class TimestampMixin:
    """Mixin adding created_at and updated_at datetime timestamps."""
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
