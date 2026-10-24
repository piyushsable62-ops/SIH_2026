"""Synthetic B2 scenarios; no production seed or Member A files are modified."""

from dataclasses import replace
from datetime import date
import json
from types import ModuleType
from uuid import uuid4

from flask_login import login_user
import pytest
from sqlalchemy import event, select

from extensions import db
from models import AuditLog, DataSubmission, IdentityGraphEdge, IdentityMatch, Institution
from models import Programme, RiskCase, StudentRecord, StudentRecordHistory, SubmissionError, SystemPolicy, User
from services import identity_resolution as identity


@pytest.fixture
def data(app):
    institutions, programmes = [], []
    for code in ("VESIT", "COEP", "VJTI"):
        institution = Institution(code=code, name=f"Synthetic {code}")
        db.session.add(institution)
        db.session.flush()
        programme = Programme(institution_id=institution.id, code="BTECH-CS", name="Computer Engineering")
        db.session.add(programme)
        db.session.flush()
        institutions.append(institution)
        programmes.append(programme)
    db.session.commit()
    counter = 0

    def student(name="Arjun Mehta", apaar="100000000001", institution=0, **kwargs):
        nonlocal counter
        counter += 1
        values = dict(institution_id=institutions[institution].id, programme_id=programmes[institution].id,
            institution_student_id=f"REG-{counter}", full_name=name, normalized_name=identity.normalize_name(name),
            apaar_ref=apaar, academic_year="2026-27", admission_date=date(2026, 6, 15))
        row = StudentRecord(**(values | kwargs))
        db.session.add(row)
        return row

    return institutions, programmes, student


@pytest.fixture
def accounts(data):
    institutions, _, _ = data
    users = [User(username="AICTE_Admin", full_name="Synthetic AICTE Admin", role="AICTE_ADMIN", password_hash="unused-test-hash")]
    users += [User(username=institution.code, full_name=f"Synthetic {institution.code}", role="INSTITUTION_ADMIN",
                   institution_id=institution.id, password_hash="unused-test-hash") for institution in institutions]
    db.session.add_all(users)
    db.session.commit()
    return users


def pair(data, **kwargs):
    """Create a cross-institution pair with no fuzzy-name similarity."""
    _, _, student = data
    a = student()
    b = student("Tanvi Patil", institution=1, **kwargs)
    db.session.commit()
    return a, b


def snapshot(id_="a", **kwargs):
    """Build pure detector inputs, including conflicts A1 cannot persist."""
    defaults = dict(id=id_, institution_id="institution-a", institution_student_id=f"REG-{id_}",
        academic_year="2026-27", programme_id="programme-a", normalized_name="rohan deshmukh",
        admission_date="2026-06-15", apaar_ref="100000000001")
    return identity.StudentSnapshot(**(defaults | kwargs))


def test_expected_seeded_events_and_no_student_mutations(data):
    _, _, student = data
    student()
    student("Tanvi Patil", institution=1)
    student("Rohan Deshmukh", "100000000002")
    student("Rohann Deshmukh", "100000000003")
    student("Zoya Sharma", "not-an-apaar")
    db.session.commit()
    before = {row.id: row.to_dict() for row in StudentRecord.query.all()}
    result = identity.scan()
    assert result.stats["by_rule"] == {"DUPLICATE_APAAR": 1, "SIMULTANEOUS_ENROLMENT": 1,
        "SIMILAR_NAME_CORROBORATED": 1, "MISSING_OR_MALFORMED_APAAR": 1}
    assert result.stats["matches"] == 3
    assert {row.id: row.to_dict() for row in StudentRecord.query.all()} == before
    assert StudentRecordHistory.query.count() == 0
    assert RiskCase.query.count() == 0
    assert all(edge["explanation"].strip() for edge in result.edges)
    assert result.started_at <= result.finished_at


def test_legitimate_shared_contacts_and_fee_payers_are_signals_only(data):
    _, _, student = data
    student("Anika Rao", contact_ref="+91 (999) 111-2222")
    student("Zubin Shah", "100000000002", contact_ref="+91 9991112222", admission_date=date(2026, 7, 1))
    student("Mira Iyer", "100000000003", fee_payer_ref="Sponsor-001")
    student("Dev Kale", "100000000004", fee_payer_ref="sponsor001", admission_date=date(2026, 8, 1))
    db.session.commit()
    result = identity.scan()
    assert result.stats["by_rule"] == {"SHARED_CONTACT": 1, "SHARED_FEE_PAYER": 1}
    assert all(event.is_signal and event.evidence["not_proof"] for event in result.events)
    assert result.matches == []
    assert IdentityMatch.query.count() == 0
    assert {node["type"] for node in result.nodes} == {"STUDENT", "INSTITUTION", "APAAR", "CONTACT", "FEE_PAYER"}
    assert all(edge["evidence"]["is_signal"] for edge in result.edges if edge["target_type"] in ("CONTACT", "FEE_PAYER"))


def test_similar_names_without_corroboration_produce_nothing(data):
    _, _, student = data
    student("Rohan Deshmukh")
    student("Rohann Deshmukh", "100000000002", admission_date=date(2026, 7, 1))
    db.session.commit()
    result = identity.scan()
    assert result.events == []
    assert result.stats["fuzzy_pairs_compared"] == 0


def test_configurable_threshold_and_corroboration(data):
    _, _, student = data
    student("Rohan Deshmukh", contact_ref="contact-001")
    student("Rohann Deshmukh", "100000000002", contact_ref="CONTACT001", admission_date=date(2026, 7, 1))
    db.session.commit()
    result = identity.scan()
    fuzzy = next(event for event in result.events if event.rule_key == "SIMILAR_NAME_CORROBORATED")
    assert fuzzy.evidence["corroboration"] == ["same normalized contact reference"]
    db.session.add(SystemPolicy(key="identity_name_threshold", value=100))
    db.session.commit()
    assert identity.scan().stats["by_rule"] == {"SHARED_CONTACT": 1}


def test_candidate_blocking_avoids_global_pair_comparisons():
    rows = [snapshot(str(i), programme_id=f"programme-{i}") for i in range(150)]
    stats = {}
    assert identity.detect_similar_names(rows, stats=stats) == []
    assert stats.get("fuzzy_pairs_compared", 0) == 0


def test_duplicate_institutional_id_conflict_pure_function():
    a = snapshot("a", institution_student_id="ROLL-01")
    b = snapshot("b", institution_student_id="ROLL-01", apaar_ref="100000000002")
    events = identity.detect_duplicate_institute_id([a, b])
    assert len(events) == 1
    assert events[0].rule_key == "DUPLICATE_INSTITUTE_ID"
    assert events[0].evidence["conflicting_fields"] == ["apaar_ref"]
    assert identity.detect_duplicate_institute_id([a, replace(b, apaar_ref=a.apaar_ref)]) == []
    assert identity.detect_duplicate_institute_id([a, replace(b, academic_year="2025-26")]) == []


def test_missing_malformed_and_shared_placeholders_do_not_link_identities(data):
    _, _, student = data
    student("Arjun Mehta", None)
    student("Tanvi Patil", "bad")
    student("Zoya Sharma", "bad")
    db.session.commit()
    result = identity.scan()
    assert result.stats["by_rule"] == {"MISSING_OR_MALFORMED_APAAR": 3}
    assert result.matches == []
    assert {event.evidence["reason"] for event in result.events} == {"missing", "malformed"}


def test_academic_conflicts_and_transfer_context_do_not_imply_active_overlap(data):
    _, programmes, _ = data
    a, b = pair(data, admission_date=date(2026, 7, 1), enrolment_status="WITHDRAWN")
    result = identity.scan()
    assert result.stats["by_rule"] == {"DUPLICATE_APAAR": 1, "CONFLICTING_ACADEMIC_DATA": 1}
    assert all(event.evidence["possible_transfer"] for event in result.events)
    conflict = next(event for event in result.events if event.rule_key == "CONFLICTING_ACADEMIC_DATA")
    assert conflict.evidence["conflicting_fields"] == ["admission_date", "enrolment_status"]
    # Equivalent programme UUIDs at different institutions must not create a conflict.
    assert a.programme_id != b.programme_id


def test_programme_conflict_and_year_isolation(data):
    _, programmes, _ = data
    a, b = pair(data)
    programmes[1].code = "BTECH-IT"
    db.session.commit()
    result = identity.scan()
    assert "CONFLICTING_ACADEMIC_DATA" in result.stats["by_rule"]
    b.academic_year = "2025-26"
    db.session.commit()
    assert identity.scan(academic_year="2026-27").events == []


def test_scan_is_idempotent_with_stable_row_ids_and_fresh_audit_runs(data):
    pair(data)
    first = identity.scan()
    second = identity.scan()
    assert first.scan_id != second.scan_id
    assert [event.event_key for event in first.events] == [event.event_key for event in second.events]
    assert [row["id"] for row in first.matches] == [row["id"] for row in second.matches]
    assert [row["id"] for row in first.edges] == [row["id"] for row in second.edges]
    assert IdentityMatch.query.count() == len(second.matches)
    assert IdentityGraphEdge.query.count() == len(second.edges)
    assert AuditLog.query.filter_by(action="IDENTITY_SCAN_FINISHED").count() == 2


def test_stale_projection_cleanup_does_not_delete_other_scopes(data):
    _, _, student = data
    a = student()
    b = student("Tanvi Patil")
    db.session.commit()
    global_result = identity.scan()
    local_result = identity.scan(institution_id=a.institution_id)
    global_ids = {row["id"] for row in global_result.matches + global_result.edges}
    b.apaar_ref = "100000000002"
    db.session.commit()
    refreshed = identity.scan(institution_id=a.institution_id)
    assert refreshed.matches == []
    assert not {row["id"] for row in local_result.matches}.intersection(row.id for row in IdentityMatch.query.all())
    assert global_ids.issubset({row.id for row in IdentityMatch.query.all()} | {row.id for row in IdentityGraphEdge.query.all()})


def test_no_raw_references_in_returned_data_graph_storage_or_audit(data, caplog):
    _, _, student = data
    student("Arjun Mehta", contact_ref="synthetic@example.invalid", fee_payer_ref="SPONSOR-SECRET")
    student("Tanvi Patil", institution=1)
    db.session.commit()
    result = identity.scan()
    serialized = json.dumps(result.to_dict())
    stored = json.dumps([row.to_dict() for row in IdentityGraphEdge.query.all()] + [row.to_dict() for row in IdentityMatch.query.all()] + [row.to_dict() for row in AuditLog.query.all()])
    for secret in ("100000000001", "synthetic@example.invalid", "sponsorsecret", "SPONSOR-SECRET"):
        assert secret not in serialized + stored + caplog.text
    assert "XXXXXXXX0001" in serialized
    assert all(edge["source"] in {node["id"] for node in result.nodes} and edge["target"] in {node["id"] for node in result.nodes} for edge in result.edges)


def test_institution_scope_queries_and_results_never_read_other_institutions(app, data, accounts):
    institutions, _, student = data
    a, b = pair(data)
    student("Zoya Sharma", "100000000003", institution=2)
    db.session.commit()
    statements = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT") and "FROM student_records" in statement:
            statements.append((statement, parameters))

    with app.test_request_context("/future-scan"):
        login_user(accounts[1])
        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            result = identity.scan(academic_year="2026-27")
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)
    assert result.stats["records_read"] == 1
    assert result.events == []
    assert {node["entity_id"] for node in result.nodes if node["type"] == "STUDENT"} == {a.id}
    assert statements
    assert all("student_records.institution_id =" in sql and institutions[0].id in params for sql, params in statements)


def test_aicte_focus_finds_cross_institution_conflicts_only_involving_focus(app, data, accounts):
    institutions, _, student = data
    a, b = pair(data)
    student("Zoya Sharma", "100000000002", institution=1)
    student("Dev Kale", "100000000002", institution=2)
    db.session.commit()
    with app.test_request_context("/future-scan"):
        login_user(accounts[0])
        result = identity.scan(institution_id=institutions[0].id)
    assert result.stats["by_rule"] == {"DUPLICATE_APAAR": 1, "SIMULTANEOUS_ENROLMENT": 1}
    assert all(a.id in item.student_ids for item in result.events)
    assert {node["entity_id"] for node in result.nodes if node["type"] == "STUDENT"} == {a.id, b.id}
    audit = AuditLog.query.filter_by(entity_id=result.scan_id).one()
    assert audit.actor_id == "AICTE_Admin"
    assert audit.actor_role == "AICTE_ADMIN"


def test_institution_cannot_override_scope_and_anonymous_cannot_scan(app, data, accounts):
    with app.test_request_context("/future-scan"):
        with pytest.raises(PermissionError):
            identity.scan()
    with app.test_request_context("/future-scan"):
        login_user(accounts[1])
        with pytest.raises(PermissionError):
            identity.scan(institution_id=data[0][1].id)
    assert IdentityMatch.query.count() == IdentityGraphEdge.query.count() == 0


def batch(data, reference="SUB-20261024-000001", hash_="a" * 64):
    """Create one synthetic submission, not a second copy of an identical hash."""
    row = DataSubmission(reference=reference, institution_id=data[0][0].id, uploaded_by="VESIT",
                         filename="synthetic.csv", file_sha256=hash_, status="COMMITTED")
    db.session.add(row)
    db.session.flush()
    return row


def test_duplicate_submission_flags_are_deduped_and_signal_only(data):
    submission = batch(data)
    data[2](source_submission_id=submission.id)
    db.session.add(SubmissionError(submission_id=submission.id, row_number=1, code="DUPLICATE_SUBMISSION", message="Synthetic duplicate retry"))
    db.session.add(AuditLog(actor_id="VESIT", action="DUPLICATE_SUBMISSION", entity_type="DATA_SUBMISSION",
        entity_id=submission.id, institution_id=submission.institution_id, details={"raw_token": "must-not-copy"}))
    db.session.commit()
    result = identity.scan(submission_id=submission.id)
    assert result.stats["by_rule"] == {"DUPLICATE_SUBMISSION": 1}
    assert result.events[0].is_signal
    assert result.matches == []
    assert "must-not-copy" not in json.dumps(result.to_dict())


def test_submission_focus_compares_existing_records_and_excludes_other_batches(data):
    submission = batch(data)
    a = data[2](source_submission_id=submission.id)
    b = data[2]("Tanvi Patil")
    db.session.commit()
    result = identity.scan(institution_id=a.institution_id, submission_id=submission.id)
    assert result.stats["anchor_records"] == 1
    assert result.stats["by_rule"] == {"DUPLICATE_APAAR": 1}
    assert result.events[0].student_ids == sorted([a.id, b.id])
    assert identity.scan(institution_id=a.institution_id, submission_id=str(uuid4())).events == []


def test_policy_invalid_input_and_pending_work_fail_without_evidence_writes(data):
    data[2]()
    with pytest.raises(ValueError, match="Commit pending"):
        identity.scan()
    db.session.commit()
    with pytest.raises(ValueError):
        identity.scan(institution_id="")
    db.session.add(SystemPolicy(key="identity_name_threshold", value=101))
    db.session.commit()
    with pytest.raises(ValueError, match="threshold"):
        identity.scan()
    assert IdentityGraphEdge.query.count() == 0
    assert AuditLog.query.count() == 0


def test_failed_audit_rolls_back_all_projection_writes(data, monkeypatch):
    pair(data)

    def fail(*args, **kwargs):
        raise RuntimeError("Synthetic audit failure")

    monkeypatch.setattr(identity.audit_service, "log", fail)
    with pytest.raises(RuntimeError, match="audit failure"):
        identity.scan()
    assert IdentityGraphEdge.query.count() == IdentityMatch.query.count() == 0
    assert StudentRecord.query.count() == 2


def test_reuses_future_a6_helpers_when_present(data, monkeypatch):
    import sys
    module = ModuleType("services.apaar_adapter")
    calls = []

    def validate(ref):
        calls.append(ref)
        return ref == "SYNTHETIC-VALID"

    module.validate_format = validate
    module.mask_apaar = lambda ref: "XXXXXXXXMASK"
    monkeypatch.setitem(sys.modules, "services.apaar_adapter", module)
    data[2](apaar="SYNTHETIC-VALID")
    db.session.commit()
    assert identity.scan().events == []
    assert "SYNTHETIC-VALID" in calls
    normalization = ModuleType("services.validation_service")
    normalization.normalize_name = lambda value: "shared-normalizer"
    monkeypatch.setitem(sys.modules, "services.validation_service", normalization)
    assert identity.normalize_name("Name") == "shared-normalizer"


def test_normalization_and_stable_event_order():
    assert identity.normalize_name("  RÓHAN--Deshmukh ") == "rohan deshmukh"
    assert identity.normalize_contact(" +91 (999) 111-2222 ") == "919991112222"
    assert identity.normalize_contact(" A.B@Example.invalid ") == "a.b@example.invalid"
    assert identity.normalize_contact("a.b@example.invalid") != identity.normalize_contact("ab@example.invalid")
    a, b = snapshot("a"), snapshot("b", institution_id="institution-b")
    assert identity.detect_duplicate_apaar([a, b])[0].event_key == identity.detect_duplicate_apaar([b, a])[0].event_key
