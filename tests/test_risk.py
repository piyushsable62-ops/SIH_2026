"""B3 deterministic scoring, persistence, access and non-punitive behavior."""

from datetime import date
from types import SimpleNamespace
from uuid import uuid4

from flask_login import login_user
import pytest

from extensions import db
from models import AdmissionEvent, AuditLog, CaseAction, Credential, RiskCase, RiskEvidence
from models import StudentRecord, StudentRecordHistory, SystemPolicy
from services import identity_resolution, risk_engine as risk
from tests.test_identity import data, accounts  # Reuse synthetic fixtures, not a second seed.


def event(key="event-a", rule="DUPLICATE_APAAR", ids=None, signal=False, **details):
    """Build a redacted, deterministic service event."""
    return {"event_key": key, "rule_key": rule, "student_ids": ids or [],
            "explanation": rule.replace("_", " ").lower(), "is_signal": signal, "evidence": details}


def result(*events):
    """Build the public ScanResult shape without requiring a persisted scan."""
    return SimpleNamespace(scan_id=str(uuid4()), events=list(events))


@pytest.mark.parametrize("rule,points", risk.DEFAULT_WEIGHTS.items())
def test_each_default_weight_applies_once(rule, points):
    score, _, rows = risk.score_events([event(rule=rule), event(rule=rule)], risk.DEFAULT_WEIGHTS)
    assert score == points
    assert len(rows) == 1 and rows[0]["points"] == points


def test_duplicate_keys_across_rules_are_order_independent():
    a, b = event(), event(rule="SIMULTANEOUS_ENROLMENT")
    assert risk.score_events([a, b], risk.DEFAULT_WEIGHTS) == risk.score_events([b, a], risk.DEFAULT_WEIGHTS)
    assert risk.score_events([a, b], risk.DEFAULT_WEIGHTS)[0] == 40


def test_redaction_preserves_numeric_uuid_segments():
    value = "00000000-0000-4000-8000-123456789012"
    assert risk._clean({"institution_id": value}) == {"institution_id": value}
    assert risk._clean("APAAR 123456789012") == "APAAR XXXXXXXX9012"


@pytest.mark.parametrize("points,severity", [(29, "LOW"), (30, "MEDIUM"), (59, "MEDIUM"),
    (60, "HIGH"), (79, "HIGH"), (80, "CRITICAL"), (100, "CRITICAL")])
def test_band_boundaries(points, severity):
    assert risk.score_events([event()], {"DUPLICATE_APAAR": points})[:2] == (points, severity)


def test_cap_and_signals_never_reach_duplicate_territory():
    assert risk.score_events([event(str(index)) for index in range(4)], risk.DEFAULT_WEIGHTS)[0] == 100
    signals = [event(str(index), "SHARED_CONTACT") for index in range(10)]
    assert risk.score_events(signals, {"SHARED_CONTACT": 1000})[:2] == (29, "LOW")
    assert risk.score_events([], {}) == (0, "LOW", [])


@pytest.mark.parametrize("weights", [{"X": -1}, {"X": True}, {"X": 0.5}, []])
def test_invalid_weights_rejected(weights):
    with pytest.raises(ValueError):
        risk.score_events([event()], weights)


def test_invalid_events_rejected():
    for broken in [{}, event(key=""), {**event(), "explanation": ""}, {**event(), "student_ids": "abc"}]:
        with pytest.raises(ValueError):
            risk.score_events([broken], risk.DEFAULT_WEIGHTS)


def test_scan_evaluate_idempotency_history_and_no_side_effects(data, caplog):
    _, _, student = data
    a, b = student(), student("Tanvi Patil", institution=1)
    db.session.flush()
    credential = Credential(reference="CRED-TEST01", student_id=a.id, institution_id=a.institution_id,
        programme_name="Synthetic programme", award_year="2026", payload_json='{"synthetic":true}',
        signature="synthetic-test-only", key_id="synthetic-key")
    db.session.add(credential)
    db.session.commit()
    before_students = {row.id: {col.name: getattr(row, col.name) for col in row.__table__.columns} for row in StudentRecord.query.all()}
    before_credential = {col.name: getattr(credential, col.name) for col in credential.__table__.columns}
    first = risk.evaluate_all(identity_resolution.scan())
    assert len(first) == 1
    case = first[0]
    assert (case.score, case.severity, case.status, case.assigned_to) == (75, "HIGH", "OPEN", None)
    assert case.reference.startswith("CASE-") and len(case.reference) == 11
    assert case.institution_id is None
    assert case.actions.one().note == "Case opened by system scan"
    assert case.recommended_action == "Request institutional clarification from both institutions and place under review"
    assert "40 duplicate APAAR + 35 simultaneous enrolment" in risk.explain(case)
    assert "risk_weights policy missing" in caplog.text
    case.status, case.assigned_to = "UNDER_REVIEW", "AICTE_Admin"
    db.session.add(CaseAction(case_id=case.id, actor_id="AICTE_Admin", action="UNDER_REVIEW",
        from_status="OPEN", to_status="UNDER_REVIEW", note="Human review"))
    db.session.commit()
    history = [item.to_dict() for item in case.actions.all()]
    ids = {item.id for item in case.evidence_items.all()}
    again = risk.evaluate_all(identity_resolution.scan())[0]
    assert again.id == case.id and RiskCase.query.count() == 1
    assert again.status == "UNDER_REVIEW" and again.assigned_to == "AICTE_Admin"
    assert history == [item.to_dict() for item in again.actions.all()]
    assert ids == {item.id for item in again.evidence_items.all()}
    assert RiskEvidence.query.count() == 2
    assert before_students == {row.id: {col.name: getattr(row, col.name) for col in row.__table__.columns} for row in StudentRecord.query.all()}
    assert before_credential == {col.name: getattr(credential, col.name) for col in credential.__table__.columns}
    assert StudentRecordHistory.query.count() == 0
    assert AuditLog.query.filter_by(action="RISK_EVALUATED").count() == 2
    metadata = again.evidence_items.first().details["_risk"]
    assert metadata["affected_student_ids"] == sorted([a.id, b.id])
    assert metadata["affected_institution_ids"] == sorted([a.institution_id, b.institution_id])


def test_custom_policies_change_score_and_bands(data):
    row = data[2]()
    db.session.commit()
    incoming = result(event(ids=[row.id]))
    case = risk.evaluate_all(incoming)[0]
    db.session.add_all([SystemPolicy(key="risk_weights", value={"DUPLICATE_APAAR": 85}),
        SystemPolicy(key="risk_bands", value={"LOW": [0, 19], "MEDIUM": [20, 39], "HIGH": [40, 89], "CRITICAL": [90, 100]})])
    db.session.commit()
    updated = risk.evaluate_all(incoming)[0]
    assert updated.id == case.id and (updated.score, updated.severity) == (85, "HIGH")
    assert updated.evidence_items.one().points == 85


def test_signals_cannot_bridge_identity_components(data):
    rows = [data[2](f"Synthetic {index}", f"{index + 1:012}") for index in range(4)]
    db.session.commit()
    a, b, c, d = [row.id for row in rows]
    cases = risk.evaluate_all(result(event("ab", ids=[a, b]), event("cd", ids=[c, d]),
        event("shared", "SHARED_CONTACT", [a, c])))
    assert len(cases) == 3
    signals = [case for case in cases if case.summary.startswith("Investigation signal")]
    assert len(signals) == 1 and (signals[0].score, signals[0].severity) == (10, "LOW")
    assert RiskEvidence.query.count() == 3


def test_cluster_growth_merges_cases_and_preserves_actions(data):
    rows = [data[2](f"Synthetic {index}", f"{index + 1:012}") for index in range(4)]
    db.session.commit()
    a, b, c, d = [row.id for row in rows]
    first = risk.evaluate_all(result(event("ab", ids=[a, b]), event("cd", ids=[c, d])))
    first[0].status, first[0].assigned_to = "RESOLVED", "AICTE_Admin"
    db.session.commit()
    survivor_id = min(first, key=lambda case: (case.opened_at, case.id)).id
    case = risk.evaluate_all(result(event("bc", "SIMULTANEOUS_ENROLMENT", [b, c])))[0]
    assert case.id == survivor_id and RiskCase.query.count() == 1
    assert (case.status, case.assigned_to) == ("RESOLVED", "AICTE_Admin")
    assert case.actions.count() == 2 and case.evidence_items.count() == 3
    assert case.score == 100
    assert risk.evaluate_all(result(event("bc", "SIMULTANEOUS_ENROLMENT", [b, c])))[0].id == case.id


def test_partial_rescan_keeps_distinct_historical_evidence(data):
    row = data[2]()
    db.session.commit()
    case = risk.evaluate_all(result(event("a", ids=[row.id]), event("b", "CONFLICTING_ACADEMIC_DATA", [row.id])))[0]
    assert case.score == 55
    assert risk.evaluate_all(result(event("a", ids=[row.id])))[0].score == 55
    assert risk.evaluate_all(result(event("a", "SIMULTANEOUS_ENROLMENT", [row.id])))[0].score == 55


def admission(data, result_value="REJECTED", reason="CAPACITY_EXCEEDED", institution=0):
    """Create a committed synthetic admission attempt without touching the ledger."""
    institutions, programmes, _ = data
    row = AdmissionEvent(institution_id=institutions[institution].id, programme_id=programmes[institution].id,
        academic_year="2026-27", result=result_value, reason_code=reason, actor_id="SYSTEM")
    db.session.add(row)
    db.session.commit()
    return row


def test_excess_intake_is_per_institution_and_idempotent(data):
    rejected = admission(data)
    case = risk.record_excess_intake_event(rejected)
    assert (case.score, case.severity, case.institution_id) == (30, "MEDIUM", rejected.institution_id)
    assert case.evidence_items.one().event_key == rejected.id
    assert risk.record_excess_intake_event(rejected).id == case.id
    assert case.score == 30 and case.actions.count() == 1
    assert risk.record_excess_intake_event(admission(data)).id == case.id
    assert case.score == 60 and case.evidence_items.count() == 2
    assert risk.record_excess_intake_event(admission(data, "ACCEPTED")) is None
    assert risk.record_excess_intake_event(admission(data, reason="VALIDATION_ERROR")) is None
    assert RiskCase.query.count() == 1


def test_authentication_and_institution_boundaries(app, data, accounts):
    own, foreign = data[2](), data[2]("Tanvi Patil", institution=1)
    db.session.commit()
    with app.test_request_context():
        with pytest.raises(PermissionError):
            risk.evaluate_all(result(event(ids=[own.id])))
    with app.test_request_context():
        login_user(accounts[1])
        assert risk.evaluate_all(result(event(ids=[own.id])))[0].institution_id == own.institution_id
        with pytest.raises(PermissionError):
            risk.evaluate_all(result(event(ids=[own.id, foreign.id])))
        with pytest.raises(PermissionError):
            risk.record_excess_intake_event(admission(data, institution=1))
    assert RiskCase.query.count() == 1


def test_audit_failure_rolls_back_and_pending_work_rejected(data, monkeypatch):
    row = data[2]()
    with pytest.raises(ValueError, match="Commit pending"):
        risk.evaluate_all(result())
    db.session.commit()
    monkeypatch.setattr(risk.audit_service, "log", lambda *args, **kwargs: {"id": None})
    with pytest.raises(RuntimeError, match="audit"):
        risk.evaluate_all(result(event(ids=[row.id])))
    assert RiskCase.query.count() == RiskEvidence.query.count() == CaseAction.query.count() == 0


def test_unknown_students_invalid_policy_and_redaction(data):
    row = data[2]()
    db.session.commit()
    with pytest.raises(PermissionError):
        risk.evaluate_all(result(event(ids=[str(uuid4())])))
    case = risk.evaluate_all(result(event(ids=[row.id], apaar_ref="123456789012", token="private-token")))[0]
    assert case.evidence_items.one().details["apaar_ref"] == "[REDACTED]"
    assert case.evidence_items.one().details["token"] == "[REDACTED]"
    db.session.add(SystemPolicy(key="risk_bands", value={"LOW": [0, 100]}))
    db.session.commit()
    with pytest.raises(ValueError, match="Risk bands"):
        risk.evaluate_all(result(event(ids=[row.id])))


def test_five_synthetic_engine_cases_cover_all_bands(data):
    """Representative fixture, not a claim that Member A's absent seed was run."""
    _, _, student = data
    student("Arjun Mehta", "100000000001")
    student("Tanvi Patil", "100000000001", institution=1)  # HIGH: 40 + 35
    student("Zoya Kale", "100000000002")
    student("Dev Shah", "100000000002", institution=1, admission_date=date(2026, 7, 1))  # CRITICAL: +15
    student("Mira Iyer", "bad")  # LOW: 20
    student("Anika Rao", "100000000003", contact_ref="shared-demo")
    student("Zubin Singh", "100000000004", contact_ref="shared-demo", admission_date=date(2026, 8, 1))
    db.session.commit()
    risk.evaluate_all(identity_resolution.scan())
    risk.record_excess_intake_event(admission(data))  # MEDIUM: 30
    assert RiskCase.query.count() == 5
    assert {case.severity for case in RiskCase.query.all()} == {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
