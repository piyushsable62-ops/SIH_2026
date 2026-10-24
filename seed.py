"""
APAAR Nexus Synthetic Reference Data Seeding Script (Task A3).
Smart India Hackathon Problem Statement PS 26263.

Initializes deterministic baseline reference data:
- Exactly 3 AICTE institutions: VESIT, COEP, VJTI
- Core degree programmes & multi-year intake capacities with category rules
- System policies (risk weights, APAAR regex format check, thresholds)
- Exactly 4 official demo user accounts
- Ed25519 development public signing key

Usage:
    python seed.py          # Seeds reference data if database is empty
    python seed.py --reset  # Drops, recreates, and seeds fresh database
"""
import os
import sys
import argparse
import random
import uuid
from typing import Dict, Any

from app import create_app
from extensions import db
from models import (
    init_db,
    Institution,
    Programme,
    IntakeCapacity,
    SystemPolicy,
    User,
    SigningKey,
    Role,
)
from services.key_store import ensure_development_key, DEFAULT_KEY_ID

# Fixed namespace for deterministic UUIDs across re-seeds
SEED_NAMESPACE = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")


def stable_uuid(entity: str, identifier: str) -> str:
    """Generate a stable, deterministic UUID5 string based on entity name and business key."""
    return str(uuid.uuid5(SEED_NAMESPACE, f"{entity}:{identifier}"))


def check_production_safety(is_reset: bool) -> None:
    """Refuse unsafe operations in production configuration."""
    flask_env = os.getenv("FLASK_ENV", "development").lower()
    allow_reset = os.getenv("ALLOW_DEMO_RESET", "true").lower() in ("true", "1", "yes")

    if flask_env == "production":
        if is_reset and not allow_reset:
            print("CRITICAL ERROR: Database reset is forbidden in production unless ALLOW_DEMO_RESET=true.")
            sys.exit(1)

        demo_password = os.getenv("DEMO_PASSWORD", "Demo@12345")
        if demo_password == "Demo@12345":
            print("CRITICAL ERROR: Production environment refuses to seed with default DEMO_PASSWORD.")
            sys.exit(1)


def seed_reference_data(rng: random.Random) -> Dict[str, int]:
    """Seed institutions, programmes, capacities, policies, demo users, and keys."""
    counts = {}

    # 1. Institutions (Exactly three, synthetic per AGENTS.md §5.2 and §5.11)
    institutions_data = [
        {
            "code": "VESIT",
            "name": "Vivekanand Education Society Institute of Technology",
            "city": "Mumbai",
            "state": "Maharashtra",
        },
        {
            "code": "COEP",
            "name": "College of Engineering Pune",
            "city": "Pune",
            "state": "Maharashtra",
        },
        {
            "code": "VJTI",
            "name": "Veermata Jijabai Technological Institute",
            "city": "Mumbai",
            "state": "Maharashtra",
        },
    ]

    institutions = {}
    for data in institutions_data:
        inst_id = stable_uuid("institution", data["code"])
        inst = Institution(
            id=inst_id,
            code=data["code"],
            name=data["name"],
            city=data["city"],
            state=data["state"],
            is_synthetic=True,
            status="ACTIVE",
        )
        db.session.add(inst)
        institutions[data["code"]] = inst

    db.session.flush()
    counts["institutions"] = len(institutions)

    # 2. Programmes (Spread across institutions)
    programmes_catalog = [
        # VESIT
        ("VESIT", "BTECH-IT", "B.Tech Information Technology", "UG"),
        ("VESIT", "BTECH-CE", "B.Tech Computer Engineering", "UG"),
        # COEP
        ("COEP", "BTECH-CE", "B.Tech Computer Engineering", "UG"),
        ("COEP", "DIP-MECH", "Diploma Mechanical Engineering", "DIPLOMA"),
        # VJTI
        ("VJTI", "BTECH-IT", "B.Tech Information Technology", "UG"),
        ("VJTI", "BTECH-EXTC", "B.Tech Electronics & Telecommunication", "UG"),
    ]

    programmes = {}
    for inst_code, code, name, level in programmes_catalog:
        inst = institutions[inst_code]
        prog_id = stable_uuid("programme", f"{inst_code}:{code}")
        prog = Programme(
            id=prog_id,
            institution_id=inst.id,
            code=code,
            name=name,
            level=level,
        )
        db.session.add(prog)
        programmes[(inst_code, code)] = prog

    db.session.flush()
    counts["programmes"] = len(programmes)

    # 3. Intake Capacities (Academic years 2024-25, 2025-26, 2026-27)
    # Default category rules: regular seats for domestic categories; supernumerary for NRI/OCI/FN
    category_rules = {
        "REGULAR": ["OPEN", "OBC", "SC", "ST", "EWS"],
        "SUPERNUMERARY": ["NRI", "OCI", "FN"],
    }

    academic_years = ["2024-25", "2025-26", "2026-27"]
    capacity_count = 0

    for (inst_code, prog_code), prog in programmes.items():

        for year in academic_years:
            cap_id = stable_uuid("capacity", f"{inst_code}:{prog_code}:{year}")

            # Specific demo scenario for VESIT / B.Tech IT / 2026-27
            if inst_code == "VESIT" and prog_code == "BTECH-IT" and year == "2026-27":
                reg_intake = 120
                sup_intake = 10
            elif prog.level == "DIPLOMA":
                reg_intake = 60
                sup_intake = 6
            else:
                reg_intake = 120
                sup_intake = 10

            cap = IntakeCapacity(
                id=cap_id,
                institution_id=prog.institution_id,
                programme_id=prog.id,
                academic_year=year,
                approved_regular_intake=reg_intake,
                supernumerary_capacity=sup_intake,
                category_rules=category_rules,
            )
            db.session.add(cap)
            capacity_count += 1

    db.session.flush()
    counts["intake_capacities"] = capacity_count

    # 4. System Policies (AGENTS.md §5.4 and §5.5)
    policies_data = [
        (
            "apaar_regex",
            r"^\d{12}$",
            "12-digit format check for synthetic APAAR IDs (not connected to live UIDAI/DigiLocker)",
        ),
        (
            "risk_weights",
            {
                "DUPLICATE_APAAR": 40,
                "SIMULTANEOUS_ENROLMENT": 35,
                "EXCESS_INTAKE_VIOLATION": 30,
                "DUPLICATE_INSTITUTE_ID": 25,
                "MISSING_OR_MALFORMED_APAAR": 20,
                "SIMILAR_NAME_CORROBORATED": 15,
                "CONFLICTING_ACADEMIC_DATA": 15,
                "SHARED_CONTACT": 10,
                "SHARED_FEE_PAYER": 10,
            },
            "Additive rule weights for explainable risk engine (capped at 100)",
        ),
        (
            "capacity_warning_thresholds",
            [80, 90, 100],
            "Dynamic Seat Ledger percentage thresholds triggering early warnings and strict blocking",
        ),
        (
            "risk_bands",
            {
                "LOW": [0, 29],
                "MEDIUM": [30, 59],
                "HIGH": [60, 79],
                "CRITICAL": [80, 100],
            },
            "Severity classification thresholds for risk investigation cases",
        ),
        ("max_upload_mb", 2, "Maximum allowed CSV file size in megabytes"),
        ("max_csv_rows", 5000, "Maximum allowed rows per CSV upload batch"),
        ("sharing_allowed_expiry_hours", [24, 72, 168], "Permitted durations for consent-based sharing tokens"),
        ("credential_default_validity_years", 4, "Standard validity period for issued student credentials"),
    ]

    for key, val, desc in policies_data:
        pol_id = stable_uuid("policy", key)
        policy = SystemPolicy(
            id=pol_id,
            key=key,
            value=val,
            description=desc,
            updated_by="SYSTEM",
        )
        db.session.add(policy)

    db.session.flush()
    counts["system_policies"] = len(policies_data)

    # 5. Users (Exactly four demo accounts per AGENTS.md §5.2 and §5.11)
    demo_password = os.getenv("DEMO_PASSWORD", "Demo@12345")
    users_data = [
        ("AICTE_Admin", "AICTE Super Administrator", Role.AICTE_ADMIN.value, None),
        ("VESIT", "VESIT Institutional Administrator", Role.INSTITUTION_ADMIN.value, institutions["VESIT"].id),
        ("COEP", "COEP Institutional Administrator", Role.INSTITUTION_ADMIN.value, institutions["COEP"].id),
        ("VJTI", "VJTI Institutional Administrator", Role.INSTITUTION_ADMIN.value, institutions["VJTI"].id),
    ]

    for username, full_name, role, inst_id in users_data:
        u_id = stable_uuid("user", username)
        user = User(
            id=u_id,
            username=username,
            full_name=full_name,
            role=role,
            institution_id=inst_id,
            is_active=True,
        )
        user.set_password(demo_password)
        db.session.add(user)

    db.session.flush()
    counts["users"] = len(users_data)

    # 6. Signing Keys (Ed25519 development public key in DB; private key in instance/keys)
    key_id, pub_pem = ensure_development_key(DEFAULT_KEY_ID)
    key_record_id = stable_uuid("signing_key", key_id)

    signing_key = SigningKey(
        id=key_record_id,
        key_id=key_id,
        public_key_pem=pub_pem,
        algorithm="Ed25519",
        is_active=True,
        is_development_key=True,
    )
    db.session.add(signing_key)
    db.session.flush()
    counts["signing_keys"] = 1

    db.session.commit()
    return counts


def run_seeding(reset: bool = False) -> None:
    """Main execution orchestrator for database seeding."""
    check_production_safety(reset)
    app = create_app()

    with app.app_context():
        if reset:
            print("Reset flag detected: Dropping all database tables...")
            db.drop_all()
            print("Recreating database schema...")
            db.create_all()
        else:
            init_db(app)
            # If not reset, check if already seeded
            if Institution.query.count() > 0:
                print("Database already contains records. Run with --reset to wipe and re-seed.")
                return

        rng = random.Random(42)
        print("Seeding reference data (Institutions, Programmes, Capacities, Policies, Users, Keys)...")
        counts = seed_reference_data(rng)

        # Hook for A4 student data generation
        try:
            from seed import seed_students
            print("Executing student scenarios hook (seed_students)...")
            student_count = seed_students(db, rng)
            counts["student_records"] = student_count
        except (ImportError, AttributeError):
            print("Note: Student generation hook (seed_students) will be populated in Task A4.")

        # Hook for Member B (Prove & Detect scenarios)
        try:
            import seed_b
            if hasattr(seed_b, "seed_prove_and_detect"):
                print("Executing Member B hook (seed_b.seed_prove_and_detect)...")
                seed_b.seed_prove_and_detect(db)
                print("Member B seeding hook executed successfully.")
        except ImportError:
            print("Note: seed_b module not found (Member B will implement this in a future task).")
        except Exception as exc:
            print(f"Warning: Member B hook encountered an error: {exc}")

        # Print formatted summary table
        print("\n" + "=" * 50)
        print("APAAR Nexus Seed Complete — Reference Summary")
        print("=" * 50)
        for entity, count in sorted(counts.items()):
            print(f"  {entity.replace('_', ' ').capitalize():<28}: {count}")
        print("=" * 50)
        print("Demo Accounts Available (Password: Demo@12345):")
        print("  - AICTE_Admin (AICTE Super Administrator)")
        print("  - VESIT       (Institution Administrator - VESIT)")
        print("  - COEP        (Institution Administrator - COEP)")
        print("  - VJTI        (Institution Administrator - VJTI)")
        print("=" * 50 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Deterministic database seeder for APAAR Nexus.")
    parser.add_argument("--reset", action="store_true", help="Drop all tables and re-seed database.")
    args = parser.parse_args()

    run_seeding(reset=args.reset)
