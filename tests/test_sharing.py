"""Consent/privacy tests reuse four existing users; no extra demo accounts."""
from datetime import timedelta
import hashlib
import logging

import pytest

from extensions import db
from models import AuditLog, ConsentRecord, SharingSession, SystemPolicy, VerificationEvent
from services import credential_service as credentials, sharing_service as sharing
from tests.test_identity import data, accounts
from tests.test_credentials import signing
from tests.test_risk import api_login


@pytest.fixture
def shared(client, signing):
    a, b, users = signing
    row = credentials.issue_credential(a.id, "2026", actor=users[0])
    other = credentials.issue_credential(b.id, "2026", actor=users[0])
    users[1].role, users[1].student_id = "STUDENT", a.id
    users[2].role, users[2].student_id = "STUDENT", b.id
    db.session.commit()
    api_login(client, users[1])
    return row, other, users, a


def create(client, shared, **kwargs):
    """Request only authenticity and award year by default."""
    return client.post("/api/sharing-sessions", json={"credential_id": shared[0].id,
        "claims": ["DEGREE_AUTHENTICITY", "AWARD_YEAR"], "expiry_hours": 24, **kwargs})


def test_create_token_once_hash_only_and_minimal_public_disclosure(client, shared, caplog):
    response = create(client, shared)
    assert response.status_code == 201, response.json
    value = response.json["data"]
    token = value["token"]
    row = SharingSession.query.one()
    assert len(token) == 43 and row.token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert row.token_prefix == token[:6] and ConsentRecord.query.one().claims == row.claims
    assert token not in str(row.to_dict()) + str(AuditLog.query.all()) + caplog.text
    public = client.get("/public/verify/" + token + "?format=json")
    assert public.status_code == 200
    assert public.json["data"]["claims"] == {"DEGREE_AUTHENTICITY": "Verified", "AWARD_YEAR": "2026"}
    assert public.headers["Cache-Control"] == "no-store" and public.headers["Referrer-Policy"] == "no-referrer"
    assert shared[3].full_name not in public.text and shared[3].apaar_ref not in public.text
    assert shared[3].id not in public.text and "Synthetic VESIT" not in public.text
    listed = client.get("/api/sharing-sessions").json
    assert token not in str(listed) and row.token_hash not in str(listed)
    assert "token" not in listed["data"][0] and listed["data"][0]["status"] == "ACTIVE"
    db.session.expire_all()
    assert row.access_count == 1 and row.last_accessed_at is not None
    assert VerificationEvent.query.filter_by(sharing_session_id=row.id).count() == 1
    assert AuditLog.query.filter_by(action="SHARING_CREATED").count() == 1


@pytest.mark.parametrize("flags", [None, True, {"STUDENT_NAME": True}, {"STUDENT_NAME": 1, "APAAR_MASKED": True}])
def test_each_sensitive_claim_needs_explicit_authorisation(client, shared, flags):
    result = create(client, shared, claims=["STUDENT_NAME", "APAAR_MASKED"], explicit_authorisation=flags)
    assert result.status_code == 422 and SharingSession.query.count() == 0


def test_selected_personal_claims_and_scope(client, shared):
    response = create(client, shared, claims=["STUDENT_NAME", "APAAR_MASKED"],
        explicit_authorisation={"STUDENT_NAME": True, "APAAR_MASKED": True})
    assert response.status_code == 201
    token = response.json["data"]["token"]
    assert client.get("/public/verify/" + token + "?format=json").json["data"]["claims"] == {"STUDENT_NAME": shared[3].full_name, "APAAR_MASKED": "XXXXXXXX0001"}
    assert create(client, shared, credential_id=shared[1].id).status_code == 404
    api_login(client, shared[2][2])
    assert client.get("/api/sharing-sessions").json["data"] == []
    assert client.post("/api/sharing-sessions/" + response.json["data"]["id"] + "/revoke", json={}).status_code == 404
    api_login(client, shared[2][0])
    assert client.get("/api/sharing-sessions").json["meta"]["total"] == 1
    assert create(client, shared).status_code == 403


@pytest.mark.parametrize("hours", [None, 0, 1, 25, 169, "24", True, -1])
def test_expiry_validation(client, shared, hours):
    assert create(client, shared, expiry_hours=hours).status_code == 422


def test_policy_narrows_expiry_and_bad_claims_fail(client, shared):
    db.session.add(SystemPolicy(key="sharing_allowed_expiry_hours", value=[24]))
    db.session.commit()
    assert create(client, shared, expiry_hours=72).status_code == 422
    for claims in ([], ["DOB"], ["AWARD_YEAR", "AWARD_YEAR"], "AWARD_YEAR", [None]):
        assert create(client, shared, claims=claims).status_code == 422
    assert SharingSession.query.count() == 0


def test_expiry_revocation_and_credential_recheck(client, shared, app):
    created = create(client, shared).json["data"]
    row = db.session.get(SharingSession, created["id"])
    app.config["SHARING_CLOCK"] = lambda: credentials.aware(row.expires_at) + timedelta(seconds=1)
    expired = client.get("/public/verify/" + created["token"] + "?format=json").json["data"]
    assert expired["result"] == "EXPIRED" and expired["claims"] == {}
    assert client.get("/api/sharing-sessions").json["data"][0]["status"] == "EXPIRED"
    app.config.pop("SHARING_CLOCK")
    assert client.post("/api/sharing-sessions/" + row.id + "/revoke", json={}).status_code == 200
    assert client.post("/api/sharing-sessions/" + row.id + "/revoke", json={}).status_code == 409
    revoked = client.get("/public/verify/" + created["token"] + "?format=json").json["data"]
    assert revoked["result"] == "REVOKED" and revoked["claims"] == {}
    fresh = create(client, shared).json["data"]
    api_login(client, shared[2][0])
    assert client.post("/api/credentials/" + shared[0].id + "/revoke", json={"reason": "Internal private support note"}).status_code == 200
    dead = client.get("/public/verify/" + fresh["token"] + "?format=json").json["data"]
    assert dead["result"] == "REVOKED" and dead["claims"] == {} and "Internal private" not in str(dead)
    api_login(client, shared[2][1])
    assert create(client, shared).status_code == 409


def test_unknown_malformed_tokens_are_uniform_and_logged_safely(client, shared, caplog):
    created = create(client, shared).json["data"]
    altered = ("A" if created["token"][0] != "A" else "B") + created["token"][1:]
    paths = [altered, "x", "bad!token", "A" * 500, "-" * 43, "bad/token"]
    results = [client.get("/public/verify/" + token + "?format=json") for token in paths]
    assert all(result.status_code == 404 and result.json == results[0].json for result in results)
    assert VerificationEvent.query.filter_by(credential_id=None).count() == len(paths)
    logger = logging.getLogger("werkzeug")
    with caplog.at_level(logging.INFO, logger="werkzeug"):
        logger.info('GET /public/verify/%s?format=json HTTP/1.1', created["token"])
        logger.info('GET /public/verify/' + created["token"] + ' HTTP/1.1')
    # Interpolated arguments must be redacted as well as literal URL messages.
    assert created["token"] not in caplog.text


def test_public_anonymous_rate_limit_and_auditor_read_only(client, shared, app):
    created = create(client, shared).json["data"]
    anonymous = app.test_client()
    for _ in range(30):
        assert anonymous.get("/public/verify/" + created["token"] + "?format=json").status_code == 200
    assert anonymous.get("/public/verify/invalid").status_code == 429
    user = shared[2][3]
    user.role, user.institution_id = "AUDITOR", None
    db.session.commit()
    api_login(client, user)
    assert client.get("/api/sharing-sessions").json["meta"]["total"] == 1
    assert client.post("/api/sharing-sessions/" + created["id"] + "/revoke", json={}).status_code == 403
    assert create(client, shared).status_code == 403


def test_audit_failure_rolls_back_consent_and_session(client, shared, monkeypatch):
    monkeypatch.setattr(sharing.audit_service, "log", lambda *args, **kwargs: {"id": None})
    assert create(client, shared).status_code == 500
    assert ConsentRecord.query.count() == SharingSession.query.count() == 0
