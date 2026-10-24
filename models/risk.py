"""
Risk Cases, Evidence, and Case Action audit models for APAAR Nexus.
Used by Member B's Explainable Risk Engine & Investigation Workflow.
"""
from datetime import datetime
from extensions import db
from models.base import generate_uuid, TimestampMixin
from models.enums import RiskSeverity, CaseStatus, CaseActionType


import time

_last_risk_timestamp = 0.0


def _monotonic_utcnow():
    """Ensure strictly monotonic UTC timestamps even in sub-millisecond test loops."""
    global _last_risk_timestamp
    now = time.time()
    if now <= _last_risk_timestamp:
        now = _last_risk_timestamp + 0.001
    _last_risk_timestamp = now
    return datetime.utcfromtimestamp(now)


class RiskCase(db.Model):
    """Investigation case created by the explainable risk engine."""
    __tablename__ = "risk_cases"

    id = db.Column(db.String(36), primary_key=True, default=generate_uuid)
    reference = db.Column(db.String(30), unique=True, nullable=False, index=True)  # CASE-XXXXXX
    institution_id = db.Column(db.String(36), db.ForeignKey("institutions.id", ondelete="SET NULL"), nullable=True, index=True)
    score = db.Column(db.Integer, nullable=False, default=0)
    severity = db.Column(db.String(20), nullable=False, default=RiskSeverity.LOW.value, index=True)
    status = db.Column(db.String(30), nullable=False, default=CaseStatus.OPEN.value, index=True)
    assigned_to = db.Column(db.String(80), nullable=True, index=True)
    recommended_action = db.Column(db.String(255), nullable=True)
    summary = db.Column(db.Text, nullable=True)
    opened_at = db.Column(db.DateTime, default=_monotonic_utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    institution = db.relationship("Institution", backref=db.backref("risk_cases", lazy="dynamic"))
    evidence_items = db.relationship("RiskEvidence", backref="case", cascade="all, delete-orphan", lazy="dynamic")
    actions = db.relationship("CaseAction", backref="case", cascade="all, delete-orphan", lazy="dynamic", order_by="CaseAction.created_at.desc()")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "reference": self.reference,
            "institution_id": self.institution_id,
            "score": self.score,
            "severity": self.severity,
            "status": self.status,
            "assigned_to": self.assigned_to,
            "recommended_action": self.recommended_action,
            "summary": self.summary,
            "opened_at": self.opened_at.isoformat() if self.opened_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self) -> str:
        return f"<RiskCase {self.reference} [{self.severity}] Score:{self.score}>"


class RiskEvidence(db.Model):
    """Transparent rule-based points breakdown for a risk case."""
    __tablename__ = "risk_evidence"
    __table_args__ = (
        db.UniqueConstraint("case_id", "event_key", name="uq_case_event_key"),
    )

    id = db.Column(db.String(36), primary_key=True, default=generate_uuid)
    case_id = db.Column(db.String(36), db.ForeignKey("risk_cases.id", ondelete="CASCADE"), nullable=False, index=True)
    rule_key = db.Column(db.String(50), nullable=False, index=True)  # e.g. "DUPLICATE_APAAR"
    points = db.Column(db.Integer, nullable=False, default=0)
    event_key = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, nullable=False)
    related_student_ids = db.Column(db.JSON, nullable=True)
    details = db.Column(db.JSON, nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "case_id": self.case_id,
            "rule_key": self.rule_key,
            "points": self.points,
            "event_key": self.event_key,
            "description": self.description,
            "related_student_ids": self.related_student_ids,
            "details": self.details,
        }

    def __repr__(self) -> str:
        return f"<RiskEvidence {self.rule_key} +{self.points}>"





class CaseAction(db.Model):
    """Human-in-the-loop action log for a risk case."""
    __tablename__ = "case_actions"

    id = db.Column(db.String(36), primary_key=True, default=generate_uuid)
    case_id = db.Column(db.String(36), db.ForeignKey("risk_cases.id", ondelete="CASCADE"), nullable=False, index=True)
    actor_id = db.Column(db.String(80), nullable=False)
    action = db.Column(db.String(40), nullable=False)
    from_status = db.Column(db.String(30), nullable=False)
    to_status = db.Column(db.String(30), nullable=False)
    note = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=_monotonic_utcnow, nullable=False, index=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "case_id": self.case_id,
            "actor_id": self.actor_id,
            "action": self.action,
            "from_status": self.from_status,
            "to_status": self.to_status,
            "note": self.note,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self) -> str:
        return f"<CaseAction {self.action} on {self.case_id} by {self.actor_id}>"
