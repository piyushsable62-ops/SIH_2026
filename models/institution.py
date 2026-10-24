"""
Institution, Programme, and Intake Capacity models for APAAR Nexus.
"""
from datetime import datetime
from extensions import db
from models.base import generate_uuid, TimestampMixin


class Institution(TimestampMixin, db.Model):
    """Educational institution approved by AICTE."""
    __tablename__ = "institutions"

    id = db.Column(db.String(36), primary_key=True, default=generate_uuid)
    code = db.Column(db.String(30), unique=True, nullable=False, index=True)
    name = db.Column(db.String(255), nullable=False)
    state = db.Column(db.String(100), nullable=False, default="Maharashtra")
    city = db.Column(db.String(100), nullable=False, default="Mumbai")
    is_synthetic = db.Column(db.Boolean, default=True, nullable=False)
    status = db.Column(db.String(30), default="ACTIVE", nullable=False)

    # Relationships
    programmes = db.relationship("Programme", backref="institution", cascade="all, delete-orphan", lazy="dynamic")
    intake_capacities = db.relationship("IntakeCapacity", backref="institution", cascade="all, delete-orphan", lazy="dynamic")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "code": self.code,
            "name": self.name,
            "state": self.state,
            "city": self.city,
            "is_synthetic": self.is_synthetic,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self) -> str:
        return f"<Institution {self.code}: {self.name}>"


class Programme(TimestampMixin, db.Model):
    """Academic degree programme offered by an institution."""
    __tablename__ = "programmes"
    __table_args__ = (
        db.UniqueConstraint("institution_id", "code", name="uq_programme_inst_code"),
    )

    id = db.Column(db.String(36), primary_key=True, default=generate_uuid)
    institution_id = db.Column(db.String(36), db.ForeignKey("institutions.id", ondelete="CASCADE"), nullable=False, index=True)
    code = db.Column(db.String(50), nullable=False, index=True)
    name = db.Column(db.String(255), nullable=False)
    level = db.Column(db.String(20), default="UG", nullable=False)

    # Relationships
    capacities = db.relationship("IntakeCapacity", backref="programme", cascade="all, delete-orphan", lazy="dynamic")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "institution_id": self.institution_id,
            "code": self.code,
            "name": self.name,
            "level": self.level,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self) -> str:
        return f"<Programme {self.code} ({self.level})>"


class IntakeCapacity(TimestampMixin, db.Model):
    """Annual intake capacity and seat ledger allocation rules."""
    __tablename__ = "intake_capacities"
    __table_args__ = (
        db.UniqueConstraint("institution_id", "programme_id", "academic_year", name="uq_intake_inst_prog_year"),
    )

    id = db.Column(db.String(36), primary_key=True, default=generate_uuid)
    institution_id = db.Column(db.String(36), db.ForeignKey("institutions.id", ondelete="CASCADE"), nullable=False, index=True)
    programme_id = db.Column(db.String(36), db.ForeignKey("programmes.id", ondelete="CASCADE"), nullable=False, index=True)
    academic_year = db.Column(db.String(20), nullable=False, index=True)  # e.g. "2026-27"
    approved_regular_intake = db.Column(db.Integer, nullable=False, default=120)
    supernumerary_capacity = db.Column(db.Integer, nullable=False, default=10)
    admitted_regular = db.Column(db.Integer, nullable=False, default=0)
    admitted_supernumerary = db.Column(db.Integer, nullable=False, default=0)
    category_rules = db.Column(db.JSON, nullable=True)  # JSON mapping of category eligibility

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "institution_id": self.institution_id,
            "programme_id": self.programme_id,
            "academic_year": self.academic_year,
            "approved_regular_intake": self.approved_regular_intake,
            "supernumerary_capacity": self.supernumerary_capacity,
            "admitted_regular": self.admitted_regular,
            "admitted_supernumerary": self.admitted_supernumerary,
            "category_rules": self.category_rules,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self) -> str:
        return f"<IntakeCapacity {self.academic_year} Reg:{self.admitted_regular}/{self.approved_regular_intake} Sup:{self.admitted_supernumerary}/{self.supernumerary_capacity}>"
