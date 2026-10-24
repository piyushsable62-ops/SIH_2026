"""
User authentication and RBAC model for APAAR Nexus.
"""
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from extensions import db, login_manager
from models.base import generate_uuid, TimestampMixin
from models.enums import Role


class User(UserMixin, TimestampMixin, db.Model):
    """Application user supporting role-based access and institution scoping."""
    __tablename__ = "users"

    id = db.Column(db.String(36), primary_key=True, default=generate_uuid)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    full_name = db.Column(db.String(120), nullable=False)
    role = db.Column(db.String(30), nullable=False, default=Role.STUDENT.value, index=True)
    institution_id = db.Column(db.String(36), db.ForeignKey("institutions.id", ondelete="SET NULL"), nullable=True, index=True)
    student_id = db.Column(db.String(36), db.ForeignKey("student_records.id", ondelete="SET NULL"), nullable=True, index=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    # Relationships
    institution = db.relationship("Institution", backref=db.backref("users", lazy="dynamic"))

    def set_password(self, password: str) -> None:
        """Hash and store user password."""
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        """Verify candidate password against stored hash."""
        return check_password_hash(self.password_hash, password)

    def to_dict(self) -> dict:
        """Serialize user summary without sensitive attributes."""
        return {
            "id": self.id,
            "username": self.username,
            "full_name": self.full_name,
            "role": self.role,
            "institution_id": self.institution_id,
            "student_id": self.student_id,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self) -> str:
        return f"<User {self.username} [{self.role}]>"


@login_manager.user_loader
def load_user(user_id: str):
    """Load user by primary key for Flask-Login."""
    return db.session.get(User, user_id)

