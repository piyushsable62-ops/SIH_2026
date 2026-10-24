"""Simulate the 12-step SIH demonstration workflow using Flask test client.
Measures execution timing, validates expected responses, and reports any friction.
"""
import base64
import hashlib
import hmac
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from flask_wtf.csrf import generate_csrf
from app import create_app
from extensions import db
from models import SystemPolicy, RiskCase, Credential, SharingSession, User, Programme

def rehearse(iteration=1):
    app = create_app("development")
    client = app.test_client()
    
    print(f"\n==================================================")
    print(f"--- SIH 12-Step Rehearsal Run #{iteration} Starting ---")
    print(f"==================================================")
    start_total = time.perf_counter()
    timings = {}
    
    def post_with_csrf(path, **kwargs):
        with client:
            client.get("/login")
            token = generate_csrf()
            headers = kwargs.pop("headers", {})
            headers["X-CSRFToken"] = token
            return client.post(path, headers=headers, **kwargs)
    
    # Step 1: Login as AICTE_Admin and open dashboard
    t0 = time.perf_counter()
    res = post_with_csrf("/api/auth/login", json={"username": "AICTE_Admin", "password": "Demo@12345"})
    assert res.status_code == 200, f"Step 1 login failed: {res.json}"
    res = client.get("/dashboard")
    assert res.status_code == 200
    res = client.get("/api/dashboard/governance?academic_year=2026-27")
    assert res.status_code == 200
    timings["Step 1: AICTE Governance Dashboard"] = time.perf_counter() - t0
    print(f"Step 1 (AICTE Governance Dashboard)        : {timings['Step 1: AICTE Governance Dashboard']:.3f}s")
    
    # Step 2: Login as VESIT and preview CSV upload
    t0 = time.perf_counter()
    post_with_csrf("/api/auth/logout")
    res = post_with_csrf("/api/auth/login", json={"username": "VESIT", "password": "Demo@12345"})
    assert res.status_code == 200
    with open("sample_data/students_demo_upload.csv", "rb") as f:
        res = post_with_csrf("/api/students/import", data={"file": (f, "students_demo_upload.csv"), "is_commit": "false"}, content_type="multipart/form-data")
    assert res.status_code in (200, 422), f"Step 2 preview returned {res.status_code}: {res.json}"
    submission_id = res.json.get("data", {}).get("submission_id") or res.json.get("error", {}).get("details", {}).get("submission_id")
    timings["Step 2: CSV Validation Preview"] = time.perf_counter() - t0
    print(f"Step 2 (CSV Validation Preview)            : {timings['Step 2: CSV Validation Preview']:.3f}s (submission_id={submission_id})")
    
    # Step 3: Download error report
    t0 = time.perf_counter()
    if submission_id:
        res = client.get(f"/api/students/import/{submission_id}/errors.csv")
        assert res.status_code == 200
        assert "text/csv" in res.content_type
    else:
        res = client.get("/api/students/import/template")
        assert res.status_code == 200
    timings["Step 3: Error Report Download"] = time.perf_counter() - t0
    print(f"Step 3 (Error Report Download)             : {timings['Step 3: Error Report Download']:.3f}s")
    
    # Step 4: Login as COEP and precheck admission on full programme
    t0 = time.perf_counter()
    post_with_csrf("/api/auth/logout")
    res = post_with_csrf("/api/auth/login", json={"username": "COEP", "password": "Demo@12345"})
    assert res.status_code == 200
    with app.app_context():
        coep_user = User.query.filter_by(username="COEP").first()
        inst_id = coep_user.institution_id
        prog = Programme.query.filter_by(institution_id=inst_id, name="Diploma Mechanical Engineering").first()
        prog_id = prog.id if prog else None
    
    res = post_with_csrf("/api/admissions/precheck", json={
        "institution_id": inst_id,
        "programme_id": prog_id,
        "academic_year": "2026-27",
        "category": "OPEN",
        "admission_category": "OPEN",
        "classification": "REGULAR",
        "quota_type": "REGULAR"
    })
    assert res.status_code in (200, 409), f"Precheck returned {res.status_code}: {res.json}"
    timings["Step 4: Seat Ledger Block Precheck"] = time.perf_counter() - t0
    print(f"Step 4 (Seat Ledger Capacity Block)        : {timings['Step 4: Seat Ledger Block Precheck']:.3f}s (HTTP {res.status_code})")
    
    # Step 5: Login as AICTE_Admin and check identity graph
    t0 = time.perf_counter()
    post_with_csrf("/api/auth/logout")
    post_with_csrf("/api/auth/login", json={"username": "AICTE_Admin", "password": "Demo@12345"})
    res = client.get("/api/identity/graph?academic_year=2026-27")
    assert res.status_code == 200
    graph_data = res.json.get("data", {})
    timings["Step 5: Identity Graph Cross-Institution"] = time.perf_counter() - t0
    print(f"Step 5 (Identity Graph Resolution)         : {timings['Step 5: Identity Graph Cross-Institution']:.3f}s (nodes={len(graph_data.get('nodes', []))})")
    
    # Step 6: Review an OPEN risk case
    t0 = time.perf_counter()
    res = client.get("/api/risk/cases?status=OPEN&page=1&per_page=10")
    assert res.status_code == 200
    cases = res.json.get("data", [])
    if cases:
        target_case = cases[0]
        case_id = target_case["id"]
        res = post_with_csrf(f"/api/risk/cases/{case_id}/actions", json={"action": "UNDER_REVIEW", "note": "Rehearsal manual review"})
        assert res.status_code == 200
    res = client.get("/api/audit-logs?page=1&per_page=10")
    assert res.status_code == 200
    timings["Step 6: Risk Case Human Review"] = time.perf_counter() - t0
    print(f"Step 6 (Risk Case Human Review & Audit)    : {timings['Step 6: Risk Case Human Review']:.3f}s")
    
    # Step 7: Issue credential as VESIT
    t0 = time.perf_counter()
    post_with_csrf("/api/auth/logout")
    post_with_csrf("/api/auth/login", json={"username": "VESIT", "password": "Demo@12345"})
    res = client.get("/api/credentials/eligible-students?page=1&per_page=5")
    assert res.status_code == 200
    eligible = res.json.get("data", [])
    if eligible:
        student_id = eligible[0]["id"]
        res = post_with_csrf("/api/credentials/issue", json={"student_id": student_id, "award_year": "2026", "valid_years": 5, "acknowledge_warnings": True})
        assert res.status_code in (201, 409), f"Issue returned {res.status_code}: {res.json}"
    timings["Step 7: Credential Issuance"] = time.perf_counter() - t0
    print(f"Step 7 (Ed25519 Credential Issuance)       : {timings['Step 7: Credential Issuance']:.3f}s")
    
    # Step 8: Verify valid credential CRED-DEMO01
    t0 = time.perf_counter()
    post_with_csrf("/api/auth/logout")
    res = client.get("/verify/CRED-DEMO01")
    assert res.status_code == 200
    assert b"Credential verified" in res.data or b"VERIFIED" in res.data
    timings["Step 8: Public Verify CRED-DEMO01"] = time.perf_counter() - t0
    print(f"Step 8 (Public Verify CRED-DEMO01)         : {timings['Step 8: Public Verify CRED-DEMO01']:.3f}s")
    
    # Step 9: Verify CRED-DEMO04 (tampered), CRED-DEMO02 (revoked), CRED-DEMO03 (expired)
    t0 = time.perf_counter()
    r4 = client.get("/verify/CRED-DEMO04")
    assert r4.status_code == 200
    assert b"Invalid" in r4.data or b"INVALID" in r4.data
    r2 = client.get("/verify/CRED-DEMO02")
    assert r2.status_code == 200
    assert b"Revoked" in r2.data or b"REVOKED" in r2.data
    r3 = client.get("/verify/CRED-DEMO03")
    assert r3.status_code == 200
    assert b"Expired" in r3.data or b"EXPIRED" in r3.data
    timings["Step 9: Tampered, Revoked, Expired Verification"] = time.perf_counter() - t0
    print(f"Step 9 (Tampered, Revoked, Expired Verify) : {timings['Step 9: Tampered, Revoked, Expired Verification']:.3f}s")
    
    # Step 10 & 11: Demo sharing session token
    t0 = time.perf_counter()
    timings["Step 10: Explain Sharing & Consent"] = time.perf_counter() - t0
    print(f"Step 10 (Explain Sharing & Consent Model)  : {timings['Step 10: Explain Sharing & Consent']:.3f}s")
    
    # Step 11: Public verify token
    t0 = time.perf_counter()
    with app.app_context():
        secret = app.config["SECRET_KEY"]
        secret_bytes = secret.encode() if isinstance(secret, str) else secret
        digest = hmac.new(secret_bytes, b"APAAR-NEXUS-DEMO-SHARE:CRED-DEMO01:ACTIVE", hashlib.sha256).digest()
        demo_token = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    res = client.get(f"/public/verify/{demo_token}")
    assert res.status_code == 200, f"Token verify returned {res.status_code}: {res.data[:200]}"
    assert b"Degree authenticity" in res.data or b"AUTHENTIC" in res.data
    timings["Step 11: Selective Disclosure Public Link"] = time.perf_counter() - t0
    print(f"Step 11 (Selective Disclosure Link Verify) : {timings['Step 11: Selective Disclosure Public Link']:.3f}s")
    
    # Step 12: AICTE Reports & CSV Export
    t0 = time.perf_counter()
    post_with_csrf("/api/auth/login", json={"username": "AICTE_Admin", "password": "Demo@12345"})
    res = client.get("/api/reports/anomalies?academic_year=2026-27")
    assert res.status_code == 200
    res = client.get("/api/reports/anomalies.csv?academic_year=2026-27")
    assert res.status_code == 200
    assert "text/csv" in res.content_type
    timings["Step 12: Anomaly Report & Audit"] = time.perf_counter() - t0
    print(f"Step 12 (Anomaly Report & CSV Export)      : {timings['Step 12: Anomaly Report & Audit']:.3f}s")
    
    total_time = time.perf_counter() - start_total
    print(f"==================================================")
    print(f"--- Rehearsal Run #{iteration} Complete in {total_time:.3f}s ---")
    print(f"==================================================")
    return total_time, timings

if __name__ == "__main__":
    rehearse(1)
    rehearse(2)
