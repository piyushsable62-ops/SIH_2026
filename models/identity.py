"""
Identity Matches and Graph Edges models for APAAR Nexus.
Used by Member B's Identity Resolution & Cytoscape Graph UI.
"""
from datetime import datetime
from extensions import db
from models.base import generate_uuid, TimestampMixin


class IdentityMatch(TimestampMixin, db.Model):
    """Pairwise identity match detected by resolution engine."""
    __tablename__ = "identity_matches"
    __table_args__ = (
        db.UniqueConstraint("scan_id", "event_key", name="uq_identity_match_scan_event"),
    )

    id = db.Column(db.String(36), primary_key=True, default=generate_uuid)
    student_a_id = db.Column(db.String(36), db.ForeignKey("student_records.id", ondelete="CASCADE"), nullable=False, index=True)
    student_b_id = db.Column(db.String(36), db.ForeignKey("student_records.id", ondelete="CASCADE"), nullable=False, index=True)
    match_type = db.Column(db.String(50), nullable=False, index=True)
    confidence = db.Column(db.Float, nullable=False, default=1.0)
    evidence = db.Column(db.JSON, nullable=True)
    scan_id = db.Column(db.String(50), nullable=False, index=True)
    event_key = db.Column(db.String(100), nullable=False, index=True)

    # Relationships
    student_a = db.relationship("StudentRecord", foreign_keys=[student_a_id], backref=db.backref("matches_as_a", lazy="dynamic"))
    student_b = db.relationship("StudentRecord", foreign_keys=[student_b_id], backref=db.backref("matches_as_b", lazy="dynamic"))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "student_a_id": self.student_a_id,
            "student_b_id": self.student_b_id,
            "match_type": self.match_type,
            "confidence": self.confidence,
            "evidence": self.evidence,
            "scan_id": self.scan_id,
            "event_key": self.event_key,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self) -> str:
        return f"<IdentityMatch {self.match_type} ({self.confidence})>"


class IdentityGraphEdge(TimestampMixin, db.Model):
    """Cytoscape-compatible edge representing relationship or conflict between entities."""
    __tablename__ = "identity_graph_edges"

    id = db.Column(db.String(36), primary_key=True, default=generate_uuid)
    source_type = db.Column(db.String(30), nullable=False, default="STUDENT")
    source_id = db.Column(db.String(50), nullable=False, index=True)
    target_type = db.Column(db.String(30), nullable=False, default="STUDENT")
    target_id = db.Column(db.String(50), nullable=False, index=True)
    edge_type = db.Column(db.String(50), nullable=False, index=True)
    explanation = db.Column(db.Text, nullable=False)  # Human-readable explanation per AGENTS.md
    evidence = db.Column(db.JSON, nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "source_type": self.source_type,
            "source_id": self.source_id,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "edge_type": self.edge_type,
            "explanation": self.explanation,
            "evidence": self.evidence,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self) -> str:
        return f"<IdentityGraphEdge {self.source_id} -> {self.target_id} [{self.edge_type}]>"
