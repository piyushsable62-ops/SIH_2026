"""Credential/privacy boundary tests. No extra demo logins are seeded."""
import pytest

from extensions import db
from models import User
from tests.test_identity import data, accounts
from tests.test_credentials import signing
from tests.test_risk import api_login
from tests.test_sharing import shared, create as create_share


def test_public_sharing_html_is_minimal_anonymous_and_revocable(client, shared, app):
    created = create_share(client, shared).json["data"]
    anonymous = app.test_client()
    path = "/public/verify/" + created["token"]
    page = anonymous.get(path)
    assert page.status_code == 200 and '<h1 id="shared-result-title">Valid</h1>' in page.text
    assert "Degree authenticity" in page.text and "Award year" in page.text and "2026" in page.text
    assert 'name="robots" content="noindex"' in page.text and "no-store" in page.headers["Cache-Control"]
    for secret in (shared[3].id, shared[3].full_name, shared[3].apaar_ref, "XXXXXXXX0001", "Synthetic VESIT", "Computer Engineering", "PRIVATE KEY", created["token"]):
        assert secret not in page.text
    assert '<input' not in page.text and 'csrf-token' not in page.text
    # Hidden flashes and signed-in account names must not leak into the public view.
    with client.session_transaction() as session:
        session['_flashes'] = [('info', 'PRIVATE FLASH DO NOT DISCLOSE')]
    logged_in = client.get(path)
    assert "PRIVATE FLASH DO NOT DISCLOSE" not in logged_in.text
    assert client.post("/api/sharing-sessions/" + created["id"] + "/revoke", json={}).status_code == 200
    dead = anonymous.get(path)
    assert '<h1 id="shared-result-title">Revoked</h1>' in dead.text and "Degree authenticity" not in dead.text
    altered = path[:-1] + ("A" if path[-1] != "A" else "B")
    invalid = anonymous.get(altered)
    assert invalid.status_code == 404 and 'Invalid</h1>' in invalid.text and "Degree authenticity" not in invalid.text
    assert invalid.text == anonymous.get('/public/verify/bad!token').text


def test_sharing_page_roles_csrf_expired_html_and_verifier_receipt(client, shared, app):
    from datetime import timedelta
    from models import SharingSession, VerificationEvent
    from services import credential_service as credentials
    page = client.get('/sharing')
    assert page.status_code == 200 and b'Personal details' in page.data and b'js/sharing.js' in page.data
    created = create_share(client, shared).json['data']
    row = db.session.get(SharingSession, created['id'])
    app.config['SHARING_CLOCK'] = lambda: credentials.aware(row.expires_at) + timedelta(seconds=1)
    expired = app.test_client().get('/public/verify/' + created['token'])
    assert b'Expired</h1>' in expired.data and b'Degree authenticity' not in expired.data
    app.config.pop('SHARING_CLOCK')
    user = shared[2][3]
    user.role = 'VERIFIER'
    db.session.commit()
    api_login(client, user)
    receipt = client.get('/public/verify/' + created['token'])
    assert b'Verification recorded' in receipt.data and user.full_name not in receipt.text
    assert VerificationEvent.query.filter_by(verifier_user_id=user.id, sharing_session_id=row.id).count() == 1
    assert client.get('/sharing').status_code == 403
    api_login(client, shared[2][1])
    app.config['WTF_CSRF_ENABLED'] = True
    denied = create_share(client, shared)
    assert denied.status_code == 400


def test_credential_api_round_trip_qr_revoke_and_scope(client, signing, app, monkeypatch):
    a, b, users = signing
    api_login(client, users[1])
    response = client.post("/api/credentials/issue", json={"student_id": a.id, "award_year": "2026"})
    assert response.status_code == 201, response.json
    id_ = response.json["data"]["id"]
    assert client.get("/api/credentials").json["meta"]["total"] == 1
    detail = client.get("/api/credentials/" + id_)
    assert detail.status_code == 200 and detail.json["data"]["result"] == "VERIFIED"
    assert detail.json["data"]["qr_data_uri"].startswith("data:image/png;base64,")
    assert detail.json["data"]["verification_event_count"] == 1
    assert a.apaar_ref not in str(detail.json) + str(response.json)
    assert "payload_json" not in str(detail.json) and "PRIVATE KEY" not in str(detail.json)
    qr = client.get(f"/api/credentials/{id_}/qr.png")
    assert qr.status_code == 200 and qr.content_type == "image/png" and qr.data.startswith(b"\x89PNG\r\n\x1a\n")
    from routes import credentials
    captured = []
    original = credentials.qr_png
    monkeypatch.setattr(credentials, "qr_png", lambda url: (captured.append(url), original(url))[1])
    app.config["PUBLIC_BASE_URL"] = "https://demo.example.test/platform"
    client.get(f"/api/credentials/{id_}/qr.png")
    assert captured[0] == "https://demo.example.test/platform/verify/" + response.json["data"]["reference"]
    assert client.post(f"/api/credentials/{id_}/revoke", json={"reason": ""}).status_code == 422
    assert client.post(f"/api/credentials/{id_}/revoke", json={"reason": "Synthetic correction"}).status_code == 200
    assert client.get("/api/credentials/" + id_).json["data"]["result"] == "REVOKED"
    api_login(client, users[2])
    for path in (f"/api/credentials/{id_}", f"/api/credentials/{id_}/qr.png", f"/credentials/{id_}"):
        assert client.get(path).status_code == 404
    assert client.post("/api/credentials/issue", json={"student_id": a.id, "award_year": "2027"}).status_code == 404


def test_student_passport_and_auditor_read_only(client, signing):
    a, b, users = signing
    api_login(client, users[0])
    issued = client.post("/api/credentials/issue", json={"student_id": a.id, "award_year": "2026"}).json["data"]
    users[1].role, users[1].student_id = "STUDENT", a.id
    db.session.commit()
    api_login(client, users[1])
    passport = client.get("/api/passport/me")
    assert passport.status_code == 200 and len(passport.json["data"]["credentials"]) == 1
    assert a.apaar_ref not in str(passport.json)
    assert not {"dob", "address", "contact_ref", "fee_payer_ref", "id"}.intersection(passport.json["data"]["profile"])
    assert client.get("/passport").status_code == 200
    assert client.post("/api/credentials/issue", json={"student_id": a.id, "award_year": "2027"}).status_code == 403
    users[1].student_id = b.id
    db.session.commit()
    assert client.get("/api/credentials/" + issued["id"]).status_code == 404
    users[3].role, users[3].institution_id = "AUDITOR", None
    db.session.commit()
    api_login(client, users[3])
    assert client.get("/api/credentials").json["meta"]["total"] == 1
    assert client.post(f"/api/credentials/{issued['id']}/revoke", json={"reason": "No"}).status_code == 403


def test_credential_json_validation_csrf_and_anonymous(client, signing, app):
    assert client.get("/api/credentials").status_code == 401
    api_login(client, signing[2][0])
    assert client.post("/api/credentials/issue", json=[]).status_code == 422
    assert client.get("/api/credentials?status=NOPE").status_code == 422
    assert client.get("/api/credentials?page=abc").status_code == 422
    eligible = client.get("/api/credentials/eligible-students")
    assert eligible.status_code == 200 and eligible.json["meta"]["total"] == 2
    assert signing[0].apaar_ref not in str(eligible.json)
    assert client.get("/credentials/issue").status_code == 200
    app.config["WTF_CSRF_ENABLED"] = True
    assert client.post("/api/credentials/issue", json={"student_id": signing[0].id, "award_year": "2026"}).status_code == 400
