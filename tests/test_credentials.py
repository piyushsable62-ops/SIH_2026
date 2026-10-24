"""Real Ed25519 service tests with synthetic students and temporary key files."""
import base64
from datetime import datetime, timedelta, timezone
import json

import pytest
from flask_login import login_user

from extensions import db
from models import AuditLog, Credential, RiskCase, RiskEvidence, SigningKey, VerificationEvent
from services import credential_service as service, key_store
from tests.test_identity import data, accounts


@pytest.fixture
def signing(app, tmp_path, data, accounts):
    app.config["SIGNING_KEY_DIR"] = str(tmp_path)
    key_id, public = key_store.ensure_development_key()
    db.session.add(SigningKey(key_id=key_id, public_key_pem=public))
    a = data[2](validation_status="VALID")
    b = data[2]("Tanvi Patil", "100000000002", institution=1, validation_status="VALID")
    db.session.commit()
    return a, b, accounts


def issue(signing, **kwargs):
    """Issue using the persisted national administrator, without a fake system bypass."""
    a, _, users = signing
    return service.issue_credential(a.id, "2026", actor=users[0], **kwargs)


def test_valid_signature_privacy_and_verification_events(signing, caplog):
    row = issue(signing)
    payload = json.loads(row.payload_json)
    assert set(payload) == service.PAYLOAD_KEYS and payload["synthetic"] is True
    assert signing[0].apaar_ref not in row.payload_json
    assert not {"dob", "address", "contact", "contact_ref", "fee_payer_ref"}.intersection(payload)
    assert service.canonicalize(payload) == row.payload_json.encode()
    result = service.verify_credential(row.reference)
    assert result.result == "VERIFIED" and "student_ref" not in result.credential
    assert VerificationEvent.query.count() == 1 and len(VerificationEvent.query.one().ip_hash) == 64
    assert "PRIVATE KEY" not in str(result.to_dict()) + row.payload_json + caplog.text
    assert AuditLog.query.filter_by(action="CREDENTIAL_ISSUED").count() == 1
    assert service.canonicalize({"é": "भारत", "a": 1}) == '{"a":1,"é":"भारत"}'.encode()


@pytest.mark.parametrize("field,value", [("signature", "not-base64"), ("payload_json", '{"award_year":"2099"}'),
    ("award_year", "2099"), ("programme_name", "Altered degree"), ("key_id", "unknown")])
def test_tampering_is_invalid_and_never_returns_claims(signing, field, value):
    row = issue(signing)
    setattr(row, field, value)
    db.session.commit()
    outcome = service.verify_credential(row.reference)
    assert outcome.result == "INVALID" and outcome.credential == {}


def test_checks_signature_before_revoked_or_expired(signing):
    row = issue(signing)
    service.revoke_credential(row.id, "Internal note must not be public", signing[2][0])
    assert service.verify_credential(row.reference).result == "REVOKED"
    assert "Internal note" not in str(service.verify_credential(row.reference).to_dict())
    row.signature = base64.b64encode(bytes(64)).decode()
    db.session.commit()
    assert service.verify_credential(row.reference).result == "INVALID"


def test_expiry_uses_server_clock_and_unknown_is_recorded(app, signing):
    row = issue(signing)
    app.config["CREDENTIAL_CLOCK"] = lambda: service.aware(row.expires_at) + timedelta(seconds=1)
    assert service.verify_credential(row.reference).result == "EXPIRED"
    assert service.verify_credential("CRED-UNKNOWN").result == "INVALID"
    assert service.verify_credential(None).reason == "not found"
    assert VerificationEvent.query.count() == 3
    assert VerificationEvent.query.filter_by(credential_id=None).count() == 2


def test_issue_permissions_eligibility_and_actor_spoofing(app, signing):
    a, b, users = signing
    with app.test_request_context():
        login_user(users[1])
        row = service.issue_credential(a.id, "2026", actor=users[1])
        assert row.institution_id == a.institution_id
        with pytest.raises(service.CredentialError):
            service.issue_credential(b.id, "2026", actor=users[1])
        with pytest.raises(service.CredentialError, match="Actor"):
            service.issue_credential(a.id, "2027", actor=users[0])
    users[2].username = "NOT_SEEDED"
    db.session.commit()
    with pytest.raises(service.CredentialError):
        service.issue_credential(b.id, "2026", actor=users[2])
    users[2].username, users[2].role = "COEP", "STUDENT"
    db.session.commit()
    with pytest.raises(service.CredentialError):
        service.issue_credential(b.id, "2026", actor=users[2])


@pytest.mark.parametrize("status,validation", [("WITHDRAWN", "VALID"), ("ACTIVE", "INVALID"), ("ACTIVE", "PENDING")])
def test_ineligible_students_rejected(signing, status, validation):
    signing[0].enrolment_status, signing[0].validation_status = status, validation
    db.session.commit()
    with pytest.raises(service.CredentialError):
        issue(signing)
    assert Credential.query.count() == 0


def test_risk_warning_requires_acknowledgement_not_automatic_denial(signing):
    a = signing[0]
    case = RiskCase(reference="CASE-TEST01", institution_id=a.institution_id, score=100, severity="CRITICAL", status="UNDER_REVIEW")
    db.session.add(case)
    db.session.flush()
    db.session.add(RiskEvidence(case_id=case.id, rule_key="DUPLICATE_APAAR", points=40, event_key="event", description="Synthetic evidence", related_student_ids=[a.id]))
    db.session.commit()
    with pytest.raises(service.CredentialError) as failure:
        issue(signing)
    assert failure.value.status == 409 and failure.value.details["warnings"]
    assert Credential.query.count() == 0
    row = issue(signing, acknowledge_warnings=True)
    assert row.warnings and service.verify_credential(row.reference).result == "VERIFIED"
    assert a.validation_status == "VALID" and case.status == "UNDER_REVIEW"


def test_revoke_reason_scope_duplicate_and_issue_duplicate(signing):
    row = issue(signing)
    with pytest.raises(service.CredentialError) as duplicate:
        issue(signing)
    assert duplicate.value.status == 409
    with pytest.raises(service.CredentialError):
        service.revoke_credential(row.id, " ", signing[2][0])
    with pytest.raises(service.CredentialError):
        service.revoke_credential(row.id, "Foreign admin", signing[2][2])
    service.revoke_credential(row.id, "Synthetic correction", signing[2][1])
    with pytest.raises(service.CredentialError) as revoked:
        service.revoke_credential(row.id, "Again", signing[2][0])
    assert revoked.value.code == "CONFLICT"


def test_key_rotation_verification_never_loads_private_key(signing, monkeypatch):
    row = issue(signing)
    key = SigningKey.query.one()
    key.is_active = False
    db.session.commit()
    monkeypatch.setattr(key_store, "load_private_key", lambda *args: pytest.fail("Verification requested a private key"))
    assert service.verify_credential(row.reference).result == "VERIFIED"


def test_audit_failure_rolls_back_issue_and_revoke(signing, monkeypatch):
    row = issue(signing)
    monkeypatch.setattr(service.audit_service, "log", lambda *args, **kwargs: {"id": None})
    with pytest.raises(RuntimeError):
        service.revoke_credential(row.id, "Synthetic", signing[2][0])
    assert db.session.get(Credential, row.id).status_stored == "ISSUED"
    with pytest.raises(RuntimeError):
        service.issue_credential(signing[1].id, "2026", actor=signing[2][0])
    assert Credential.query.count() == 1


def test_public_four_states_demo_seed_and_no_claims_for_tampering(client, signing):
    from seed_b import seed_demo_credentials
    refs = seed_demo_credentials()
    assert refs == seed_demo_credentials() and Credential.query.count() == 4
    for reference, status, text in zip(refs, ["VERIFIED", "REVOKED", "EXPIRED", "INVALID"],
            ["Credential verified", "Credential revoked", "Credential expired", "Credential invalid"]):
        page = client.get("/verify/" + reference)
        assert page.status_code == 200 and text in page.get_data(as_text=True)
        assert page.headers["Cache-Control"] == "no-store"
        result = client.get("/verify/" + reference + ".json")
        assert result.json["data"]["result"] == status
        assert signing[0].apaar_ref not in page.get_data(as_text=True)
        if status == "INVALID":
            assert result.json["data"]["credential"] == {}
            assert "DEMO ALTERED" not in page.get_data(as_text=True)
            assert "Computer Engineering" not in page.get_data(as_text=True)
    assert VerificationEvent.query.count() == 8
    unknown = client.get("/verify/CRED-NOTFOUND")
    assert unknown.status_code == 200 and "not found" in unknown.get_data(as_text=True)


def test_public_rate_limit_shared_across_html_and_json(client, signing, app):
    app.config["PUBLIC_RATE_CLOCK"] = lambda: 100.0
    for index in range(30):
        assert client.get("/verify/CRED-NOTFOUND" + (".json" if index % 2 else "")).status_code == 200
    assert client.get("/verify/CRED-OTHER").status_code == 429
    app.config["PUBLIC_RATE_CLOCK"] = lambda: 161.0
    assert client.get("/verify/CRED-OTHER").status_code == 200


def test_verify_console_login_demo_toggle_and_own_recent_events(client, signing, app):
    from tests.test_risk import api_login
    from seed_b import seed_demo_credentials
    refs = seed_demo_credentials()
    assert client.get("/verify-console").status_code == 302
    api_login(client, signing[2][0])
    page = client.get("/verify-console")
    assert all(reference in page.get_data(as_text=True) for reference in refs)
    redirect = client.get("/verify-console?reference=CRED-DEMO01")
    assert redirect.status_code == 302 and redirect.location.endswith("/verify/CRED-DEMO01")
    assert client.get("/verify-console?reference=https://evil.example/verify/CRED-DEMO01").status_code == 422
    app.config["SHOW_DEMO_ACCOUNTS"] = False
    assert "Demo credentials" not in client.get("/verify-console").get_data(as_text=True)
