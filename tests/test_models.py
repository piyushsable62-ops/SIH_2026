"""
Unit and smoke tests for all SQLAlchemy models in APAAR Nexus.
Verifies table creation, relationships, foreign key enforcement, and audit persistence.
"""
import pytest
from datetime import datetime, date, timedelta
from sqlalchemy.exc import IntegrityError

from extensions import db
from models import (
    User,
    Institution,
    Programme,
    IntakeCapacity,
    StudentRecord,
    StudentRecordHistory,
    AdmissionEvent,
    DataSubmission,
    SubmissionError,
    IdentityMatch,
    IdentityGraphEdge,
    RiskCase,
    RiskEvidence,
    CaseAction,
    Credential,
    SigningKey,
    ConsentRecord,
    SharingSession,
    VerificationEvent,
    AuditLog,
    SystemPolicy,
    Role,
    Classification,
    AdmissionCategory,
    EnrolmentStatus,
    ValidationStatus,
    SubmissionStatus,
    AdmissionEventResult,
    RiskSeverity,
    CaseStatus,
    CaseActionType,
    CredentialStatus,
    VerificationResult,
    SharingClaim,
)
from services.audit_service import log as audit_log


def test_sqlite_foreign_keys_enforced(app):
    """Verify PRAGMA foreign_keys=ON is active and raises IntegrityError on orphan foreign key."""
    with app.app_context():
        # Attempt to insert a Programme with an invalid institution_id
        invalid_prog = Programme(
            institution_id="non-existent-institution-uuid",
            code="BTECH-CS",
            name="Computer Science",
            level="UG"
        )
        db.session.add(invalid_prog)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_smoke_create_all_tables(app):
    """Smoke test: create and persist valid rows in every table."""
    with app.app_context():
        # 1. Institution
        inst = Institution(
            code="VESIT",
            name="Vivekanand Education Society Institute of Technology",
            state="Maharashtra",
            city="Mumbai",
            is_synthetic=True,
            status="ACTIVE"
        )
        db.session.add(inst)
        db.session.flush()
        assert inst.id is not None

        # 2. Programme
        prog = Programme(
            institution_id=inst.id,
            code="BTECH-IT",
            name="B.Tech Information Technology",
            level="UG"
        )
        db.session.add(prog)
        db.session.flush()
        assert prog.id is not None

        # 3. IntakeCapacity
        capacity = IntakeCapacity(
            institution_id=inst.id,
            programme_id=prog.id,
            academic_year="2026-27",
            approved_regular_intake=120,
            supernumerary_capacity=10,
            category_rules={"regular": ["OPEN", "OBC", "SC", "ST", "EWS"], "supernumerary": ["NRI", "OCI", "FN"]}
        )
        db.session.add(capacity)
        db.session.flush()
        assert capacity.id is not None

        # 4. DataSubmission
        sub = DataSubmission(
            reference="SUB-20261024-000001",
            institution_id=inst.id,
            uploaded_by="vesit_admin",
            filename="students_batch_01.csv",
            file_sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            status=SubmissionStatus.COMMITTED.value,
            total_rows=1,
            valid_rows=1,
            error_rows=0,
            committed_at=datetime.utcnow()
        )
        db.session.add(sub)
        db.session.flush()
        assert sub.id is not None

        # 5. SubmissionError
        sub_err = SubmissionError(
            submission_id=sub.id,
            row_number=1,
            field="apaar_ref",
            code="INVALID_FORMAT",
            message="APAAR ID must be 12 digits",
            raw_row={"name": "Test Student", "apaar_ref": "123"}
        )
        db.session.add(sub_err)
        db.session.flush()
        assert sub_err.id is not None

        # 6. StudentRecord
        student = StudentRecord(
            institution_id=inst.id,
            programme_id=prog.id,
            institution_student_id="VESIT-2026-IT-001",
            apaar_ref="123456789012",
            full_name="Aarav Sharma",
            normalized_name="aarav sharma",
            academic_year="2026-27",
            admission_date=date.today(),
            admission_category=AdmissionCategory.OPEN.value,
            classification=Classification.REGULAR.value,
            is_nri=False,
            is_oci=False,
            is_foreign_national=False,
            enrolment_status=EnrolmentStatus.ACTIVE.value,
            validation_status=ValidationStatus.VALID.value,
            contact_ref="contact_hash_001",
            fee_payer_ref="payer_hash_001",
            source_submission_id=sub.id,
            is_synthetic=True
        )
        db.session.add(student)
        db.session.flush()
        assert student.id is not None

        # 7. StudentRecordHistory
        history = StudentRecordHistory(
            student_id=student.id,
            changed_by="vesit_admin",
            field="admission_category",
            old_value="OPEN",
            new_value="EWS",
            reason="Certificate verified"
        )
        db.session.add(history)
        db.session.flush()
        assert history.id is not None

        # 8. User
        user = User(
            username="VESIT",
            full_name="VESIT Administrator",
            role=Role.INSTITUTION_ADMIN.value,
            institution_id=inst.id,
            is_active=True
        )
        user.set_password("Demo@12345")
        db.session.add(user)
        db.session.flush()
        assert user.id is not None
        assert user.check_password("Demo@12345") is True
        assert user.check_password("WrongPassword") is False

        # 9. AdmissionEvent
        adm_event = AdmissionEvent(
            institution_id=inst.id,
            programme_id=prog.id,
            academic_year="2026-27",
            student_id=student.id,
            category=AdmissionCategory.OPEN.value,
            classification=Classification.REGULAR.value,
            result=AdmissionEventResult.ACCEPTED.value,
            reason_code="APPROVED",
            message="Admitted within capacity",
            actor_id=user.username
        )
        db.session.add(adm_event)
        db.session.flush()
        assert adm_event.id is not None

        # 10. Second Student for Identity Match
        student_2 = StudentRecord(
            institution_id=inst.id,
            programme_id=prog.id,
            institution_student_id="VESIT-2026-IT-002",
            apaar_ref="123456789012",  # Duplicate APAAR reference
            full_name="Aarav R. Sharma",
            normalized_name="aarav r sharma",
            academic_year="2026-27",
            admission_date=date.today(),
            admission_category=AdmissionCategory.OPEN.value,
            classification=Classification.REGULAR.value,
            enrolment_status=EnrolmentStatus.ACTIVE.value,
            validation_status=ValidationStatus.FLAGGED.value,
            is_synthetic=True
        )
        db.session.add(student_2)
        db.session.flush()

        # 11. IdentityMatch
        match = IdentityMatch(
            student_a_id=student.id,
            student_b_id=student_2.id,
            match_type="DUPLICATE_APAAR",
            confidence=1.0,
            evidence={"apaar_ref": "XXXXXXXX9012"},
            scan_id="SCAN-20261024-001",
            event_key="EVT-MATCH-001"
        )
        db.session.add(match)
        db.session.flush()
        assert match.id is not None

        # 12. IdentityGraphEdge
        edge = IdentityGraphEdge(
            source_type="STUDENT",
            source_id=student.id,
            target_type="STUDENT",
            target_id=student_2.id,
            edge_type="CONFLICT",
            explanation="Identical APAAR identifier claimed by two distinct student records",
            evidence={"apaar": "XXXXXXXX9012"}
        )
        db.session.add(edge)
        db.session.flush()
        assert edge.id is not None

        # 13. RiskCase
        risk_case = RiskCase(
            reference="CASE-000001",
            institution_id=inst.id,
            score=75,
            severity=RiskSeverity.HIGH.value,
            status=CaseStatus.OPEN.value,
            assigned_to=user.username,
            recommended_action="Request identity documents from institute",
            summary="High severity APAAR collision detected across records"
        )
        db.session.add(risk_case)
        db.session.flush()
        assert risk_case.id is not None

        # 14. RiskEvidence
        evidence = RiskEvidence(
            case_id=risk_case.id,
            rule_key="DUPLICATE_APAAR",
            points=40,
            event_key="EVT-APAAR-001",
            description="Two student records share the exact same APAAR identifier",
            related_student_ids=[student.id, student_2.id]
        )
        db.session.add(evidence)
        db.session.flush()
        assert evidence.id is not None

        # 15. CaseAction
        action = CaseAction(
            case_id=risk_case.id,
            actor_id=user.username,
            action=CaseActionType.UNDER_REVIEW.value,
            from_status=CaseStatus.OPEN.value,
            to_status=CaseStatus.UNDER_REVIEW.value,
            note="Investigation initiated by institutional registrar"
        )
        db.session.add(action)
        db.session.flush()
        assert action.id is not None

        # 16. SigningKey
        key = SigningKey(
            key_id="KEY-2026-DEV-01",
            public_key_pem="-----BEGIN PUBLIC KEY-----\nMCowBQYDK2VwAyEA0123456789abcdef0123456789abcdef0123456789a=\n-----END PUBLIC KEY-----",
            algorithm="Ed25519",
            is_active=True,
            is_development_key=True
        )
        db.session.add(key)
        db.session.flush()
        assert key.id is not None

        # 17. Credential
        cred = Credential(
            reference="CRED-000001",
            student_id=student.id,
            institution_id=inst.id,
            programme_name=prog.name,
            award_year="2026",
            issue_date=date.today(),
            payload_json='{"credential_id":"CRED-000001","student_name":"Aarav Sharma"}',
            signature="dGVzdF9zaWduYXR1cmVfYmFzZTY0",
            key_id=key.key_id,
            status_stored=CredentialStatus.ISSUED.value
        )
        db.session.add(cred)
        db.session.flush()
        assert cred.id is not None

        # 18. ConsentRecord
        consent = ConsentRecord(
            student_id=student.id,
            claims=[SharingClaim.DEGREE_AUTHENTICITY.value, SharingClaim.AWARD_YEAR.value]
        )
        db.session.add(consent)
        db.session.flush()
        assert consent.id is not None

        # 19. SharingSession
        session = SharingSession(
            student_id=student.id,
            credential_id=cred.id,
            consent_id=consent.id,
            token_hash="5e884898da28047151d0e56f8dc6292773603d0d6aabbdd62a11ef721d1542d8",
            token_prefix="tok_ab",
            claims=[SharingClaim.DEGREE_AUTHENTICITY.value],
            expires_at=datetime.utcnow() + timedelta(hours=24)
        )
        db.session.add(session)
        db.session.flush()
        assert session.id is not None

        # 20. VerificationEvent
        verif = VerificationEvent(
            credential_id=cred.id,
            sharing_session_id=session.id,
            result=VerificationResult.VERIFIED.value,
            ip_hash="ip_hash_123"
        )
        db.session.add(verif)
        db.session.flush()
        assert verif.id is not None

        # 21. SystemPolicy
        policy = SystemPolicy(
            key="apaar_regex",
            value=r"^\d{12}$",
            description="12 digit APAAR reference format check regex",
            updated_by="SYSTEM"
        )
        db.session.add(policy)
        db.session.flush()
        assert policy.id is not None

        # 22. AuditLog via audit_service.log with sensitive data sanitization
        audit_res = audit_log(
            actor="vesit_admin",
            action="CREDENTIAL_ISSUED",
            entity_type="CREDENTIAL",
            entity_id=cred.id,
            details={
                "programme": prog.code,
                "password": "super-secret-password-should-be-scrubbed",
                "token": "secret-token-12345",
                "apaar_ref": "123456789012"
            },
            institution_id=inst.id
        )
        assert audit_res["id"] is not None

        # Verify audit log in database
        persisted_audit = db.session.get(AuditLog, audit_res["id"])
        assert persisted_audit is not None
        assert persisted_audit.actor_id == "vesit_admin"
        assert persisted_audit.action == "CREDENTIAL_ISSUED"
        assert persisted_audit.details["password"] == "[REDACTED]"
        assert persisted_audit.details["token"] == "[REDACTED]"
        assert persisted_audit.details["apaar_ref"] == "XXXXXXXX9012"

        db.session.commit()
