"""Real synthetic Detect and Prove seed integration, without extra accounts."""
import random

from extensions import db
from models import AuditLog, CaseAction, ConsentRecord, Credential, RiskCase, SharingSession, User
from seed import seed_reference_data, seed_students
from seed_b import seed_prove_and_detect
from services import credential_service, sharing_service
from services.seat_ledger import recompute_counters


def test_seed_b_states_idempotence_and_summary(app, capsys):
    with app.app_context():
        seed_reference_data(random.Random(42))
        seed_students(db, random.Random(42))
        recompute_counters()
        seed_prove_and_detect(db)
        first = capsys.readouterr().out
        assert first.count("Demo-only sharing token") == 1
        token = first.split("Demo-only sharing token (shown once; copy now): ", 1)[1].splitlines()[0]
        assert len(token) == 43
        assert User.query.count() == 4
        assert {"LOW", "MEDIUM", "CRITICAL"} <= {case.severity for case in RiskCase.query.all()}
        assert RiskCase.query.count() >= 5
        assert CaseAction.query.filter_by(action="UNDER_REVIEW", actor_id="AICTE_Admin", to_status="UNDER_REVIEW").count() == 1
        assert AuditLog.query.filter_by(action="RISK_CASE_ACTION").count() == 1
        states = {row.reference: credential_service.check_credential(row).result for row in Credential.query.all()}
        assert states == {"CRED-DEMO01": "VERIFIED", "CRED-DEMO02": "REVOKED",
                          "CRED-DEMO03": "EXPIRED", "CRED-DEMO04": "INVALID"}
        sessions = SharingSession.query.all()
        assert len(sessions) == 3 and ConsentRecord.query.count() == 3
        assert {sharing_service.session_status(row) for row in sessions} == {"ACTIVE", "EXPIRED", "REVOKED"}
        assert token not in str([(row.token_hash, row.token_prefix) for row in sessions])
        assert token not in str([row.details for row in AuditLog.query.all()])
        resolved = sharing_service.resolve_token(token)
        assert resolved["claims"] == {"DEGREE_AUTHENTICITY": "Verified", "AWARD_YEAR": Credential.query.filter_by(reference="CRED-DEMO01").one().award_year}
        counts = (RiskCase.query.count(), Credential.query.count(), SharingSession.query.count())
        seed_prove_and_detect(db)
        second = capsys.readouterr().out
        assert "Demo-only sharing token" not in second
        assert counts == (RiskCase.query.count(), Credential.query.count(), SharingSession.query.count())
