"""
Tests for reference data seeding (Task A3).
Verifies determinism, exact account counts, password security, and key store functionality.
"""
import random
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives import serialization

from extensions import db
from models import (
    Institution,
    Programme,
    IntakeCapacity,
    SystemPolicy,
    User,
    SigningKey,
    Role,
)
from seed import seed_reference_data
from services.key_store import load_private_key, get_active_key_id
from services.authz import roles_required


def test_seed_exact_counts_and_institutions(app):
    """
    Verify seed contract from AGENTS.md §5.2 and §5.11:
    - Exactly 3 institutions (VESIT, COEP, VJTI)
    - Exactly 4 demo accounts (AICTE_Admin, VESIT, COEP, VJTI)
    - No student, verifier, or auditor accounts seeded
    """
    with app.app_context():
        seed_reference_data(random.Random(42))

        # Institutions check
        institutions = Institution.query.all()
        assert len(institutions) == 3
        inst_codes = {inst.code for inst in institutions}
        assert inst_codes == {"VESIT", "COEP", "VJTI"}

        for inst in institutions:
            assert inst.is_synthetic is True
            assert inst.status == "ACTIVE"

        # Users check
        users = User.query.all()
        assert len(users) == 4
        usernames = {u.username for u in users}
        assert usernames == {"AICTE_Admin", "VESIT", "COEP", "VJTI"}

        user_roles = {u.username: u.role for u in users}
        assert user_roles["AICTE_Admin"] == Role.AICTE_ADMIN.value
        assert user_roles["VESIT"] == Role.INSTITUTION_ADMIN.value
        assert user_roles["COEP"] == Role.INSTITUTION_ADMIN.value
        assert user_roles["VJTI"] == Role.INSTITUTION_ADMIN.value

        # Scoping check
        vesit_inst = Institution.query.filter_by(code="VESIT").first()
        vesit_user = User.query.filter_by(username="VESIT").first()
        assert vesit_user.institution_id == vesit_inst.id

        aicte_user = User.query.filter_by(username="AICTE_Admin").first()
        assert aicte_user.institution_id is None


def test_seed_determinism_produces_identical_ids(app):
    """Verify seeding twice with reset produces 100% identical primary keys and values."""
    with app.app_context():
        # First seed run
        seed_reference_data(random.Random(42))
        run1_inst = {inst.code: inst.id for inst in Institution.query.all()}
        run1_users = {u.username: u.id for u in User.query.all()}
        run1_policies = {p.key: (p.id, p.value) for p in SystemPolicy.query.all()}

        # Wipe and seed second run
        db.drop_all()
        db.create_all()
        seed_reference_data(random.Random(42))

        run2_inst = {inst.code: inst.id for inst in Institution.query.all()}
        run2_users = {u.username: u.id for u in User.query.all()}
        run2_policies = {p.key: (p.id, p.value) for p in SystemPolicy.query.all()}

        assert run1_inst == run2_inst
        assert run1_users == run2_users
        assert run1_policies == run2_policies


def test_no_plaintext_passwords_stored(app):
    """Verify passwords are authenticated via hashes and no plaintext password exists in DB."""
    with app.app_context():
        seed_reference_data(random.Random(42))
        users = User.query.all()

        for user in users:
            assert user.password_hash != "Demo@12345"
            assert user.password_hash.startswith("scrypt:") or user.password_hash.startswith("pbkdf2:")
            assert user.check_password("Demo@12345") is True
            assert user.check_password("WrongPassword") is False


def test_all_defined_role_enums_supported_in_auth_code(app):
    """
    Verify all 5 role enums (AICTE_ADMIN, INSTITUTION_ADMIN, STUDENT, VERIFIER, AUDITOR)
    remain fully supported in authorization decorators even though only 2 roles are seeded.
    """
    with app.app_context():
        # Check that all roles defined in AGENTS.md §5.1 exist
        expected_roles = {"AICTE_ADMIN", "INSTITUTION_ADMIN", "STUDENT", "VERIFIER", "AUDITOR"}
        actual_roles = {r.value for r in Role}
        assert expected_roles == actual_roles

        # Ensure decorator accepts all role enums without error
        for role in Role:
            decorator = roles_required(role)
            assert callable(decorator)


def test_signing_keys_public_in_db_private_in_keystore(app):
    """Verify DB stores public key PEM only, and private key can sign and verify."""
    with app.app_context():
        seed_reference_data(random.Random(42))

        signing_keys = SigningKey.query.all()
        assert len(signing_keys) == 1
        db_key = signing_keys[0]

        assert db_key.key_id == get_active_key_id()
        assert "BEGIN PUBLIC KEY" in db_key.public_key_pem
        assert "PRIVATE KEY" not in db_key.public_key_pem

        # Load private key from keystore helper and sign test message
        private_key = load_private_key(db_key.key_id)
        test_message = b"APAAR_NEXUS_CREDENTIAL_SIGNING_VERIFICATION"
        signature = private_key.sign(test_message)

        # Verify signature with public key loaded from database
        public_key = serialization.load_pem_public_key(db_key.public_key_pem.encode("utf-8"))
        assert isinstance(public_key, Ed25519PublicKey)
        public_key.verify(signature, test_message)  # Raises InvalidSignature if failed
