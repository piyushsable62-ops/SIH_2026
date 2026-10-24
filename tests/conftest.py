"""
Pytest configuration and shared test fixtures for APAARSetu.
Provides: app, db, client, and authenticated clients for all roles.
"""
import pytest
from app import create_app
from extensions import db as _db
from models import User, Institution, Role
from services.authz import login_rate_limiter


@pytest.fixture
def app():
    """Create and configure a testing app instance with fresh in-memory database."""
    application = create_app("testing")

    with application.app_context():
        _db.create_all()
        yield application
        _db.session.remove()
        _db.drop_all()


@pytest.fixture
def db(app):
    """Provide the SQLAlchemy database handle."""
    return _db


@pytest.fixture
def client(app):
    """Base unauthenticated test client fixture."""
    return app.test_client()


@pytest.fixture(autouse=True)
def _reset_login_rate_limits():
    """Reset the auth rate limiter before and after every test to prevent cross-test pollution."""
    login_rate_limiter.reset_all()
    yield
    login_rate_limiter.reset_all()


def _ensure_role_users(app):
    """Seed minimal institutions and users for all roles if not already present."""
    with app.app_context():
        # Check if already seeded in this context
        if User.query.filter_by(username="AICTE_Admin").first():
            return

        vesit = Institution.query.filter_by(code="VESIT").first()
        if not vesit:
            vesit = Institution(code="VESIT", name="VESIT Mumbai", state="MH", city="Mumbai")
            _db.session.add(vesit)

        coep = Institution.query.filter_by(code="COEP").first()
        if not coep:
            coep = Institution(code="COEP", name="COEP Pune", state="MH", city="Pune")
            _db.session.add(coep)

        _db.session.flush()

        users = [
            ("AICTE_Admin", Role.AICTE_ADMIN.value, None),
            ("VESIT", Role.INSTITUTION_ADMIN.value, vesit.id),
            ("COEP", Role.INSTITUTION_ADMIN.value, coep.id),
            ("student_user", Role.STUDENT.value, None),
            ("verifier_user", Role.VERIFIER.value, None),
            ("auditor_user", Role.AUDITOR.value, None),
        ]

        for username, role, inst_id in users:
            if not User.query.filter_by(username=username).first():
                user = User(
                    username=username,
                    full_name=f"{username} Test",
                    role=role,
                    institution_id=inst_id,
                    is_active=True
                )
                user.set_password("Demo@12345")
                _db.session.add(user)

        _db.session.commit()


def _login_client(client, username, password="Demo@12345"):
    """Helper to log in a test client via the API."""
    res = client.post("/api/auth/login", json={"username": username, "password": password})
    assert res.status_code == 200, f"Failed to login test fixture user {username}: {res.get_data(as_text=True)}"
    return client


@pytest.fixture
def aicte_admin_client(app):
    """Test client authenticated as AICTE_Admin (AICTE_ADMIN)."""
    _ensure_role_users(app)
    c = app.test_client()
    return _login_client(c, "AICTE_Admin")


@pytest.fixture
def vesit_client(app):
    """Test client authenticated as VESIT (INSTITUTION_ADMIN, VESIT scope)."""
    _ensure_role_users(app)
    c = app.test_client()
    return _login_client(c, "VESIT")


@pytest.fixture
def coep_client(app):
    """Test client authenticated as COEP (INSTITUTION_ADMIN, COEP scope)."""
    _ensure_role_users(app)
    c = app.test_client()
    return _login_client(c, "COEP")


@pytest.fixture
def student_client(app):
    """Test client authenticated as student_user (STUDENT)."""
    _ensure_role_users(app)
    c = app.test_client()
    return _login_client(c, "student_user")


@pytest.fixture
def verifier_client(app):
    """Test client authenticated as verifier_user (VERIFIER)."""
    _ensure_role_users(app)
    c = app.test_client()
    return _login_client(c, "verifier_user")


@pytest.fixture
def auditor_client(app):
    """Test client authenticated as auditor_user (AUDITOR)."""
    _ensure_role_users(app)
    c = app.test_client()
    return _login_client(c, "auditor_user")
