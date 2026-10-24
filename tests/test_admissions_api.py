"""
API-level tests for Seat Ledger and Admissions (Task A10).

Verifies:
1. GET /api/seats/summary (scope-aware, role-enforced).
2. GET /api/seats/ledger (detailed ledger with category breakdown and utilisation).
3. POST /api/admissions/precheck (returns allowed/blocked, writes audit log on blocked).
4. POST /api/admissions:
   - 201 on successful commit.
   - 409 CAPACITY_EXCEEDED on admission beyond approved capacity.
   - 409 DUPLICATE_ADMISSION on duplicate institutional student ID or APAAR.
   - 409 CATEGORY_NOT_ELIGIBLE on invalid category for classification.
   - 403 FORBIDDEN when institution admin attempts admission into another institution.
5. GET /api/admissions/events:
   - Paginated admission history.
   - Displays both ACCEPTED and REJECTED attempts with reason code.
   - Scoped to user's institution.
6. Integration with POST /api/students:
   - Manual entry consumes seat via seat ledger and returns 409 when full.
7. Audit log entries are written for admissions and rejected prechecks.
"""
import pytest
from datetime import date
from extensions import db
from models.institution import Institution, Programme, IntakeCapacity
from models.student import StudentRecord
from models.admission import AdmissionEvent
from models.user import User
from models.policy import SystemPolicy
from models.audit import AuditLog
from models.enums import Role, AdmissionEventResult


@pytest.fixture
def a10_fixture(app):
    """Seed institutions, programmes, intake capacities, users, and policy."""
    with app.app_context():
        # Clear tables
        db.session.query(AdmissionEvent).delete()
        db.session.query(StudentRecord).delete()
        db.session.query(IntakeCapacity).delete()
        db.session.query(Programme).delete()
        db.session.query(Institution).delete()
        db.session.query(User).delete()
        db.session.query(SystemPolicy).delete()
        db.session.query(AuditLog).delete()
        db.session.commit()

        # Seed institutions
        vesit = Institution(code="VESIT", name="VESIT Mumbai", state="Maharashtra", city="Mumbai", status="ACTIVE")
        coep = Institution(code="COEP", name="COEP Pune", state="Maharashtra", city="Pune", status="ACTIVE")
        db.session.add_all([vesit, coep])
        db.session.flush()

        # Seed programmes
        vesit_it = Programme(institution_id=vesit.id, code="BTECH-IT", name="B.Tech IT", level="UG")
        coep_cs = Programme(institution_id=coep.id, code="BTECH-CS", name="B.Tech CS", level="UG")
        db.session.add_all([vesit_it, coep_cs])
        db.session.flush()

        # Seed intake capacities
        # VESIT IT: 120 regular, 10 supernumerary
        vesit_cap = IntakeCapacity(
            institution_id=vesit.id,
            programme_id=vesit_it.id,
            academic_year="2026-27",
            approved_regular_intake=120,
            supernumerary_capacity=10,
            admitted_regular=0,
            admitted_supernumerary=0,
            category_rules={
                "REGULAR": ["OPEN", "OBC", "SC", "ST", "EWS"],
                "SUPERNUMERARY": ["NRI", "OCI", "FN"],
            },
        )
        # COEP CS: 2 regular (small for easy full-capacity testing), 2 supernumerary
        coep_cap = IntakeCapacity(
            institution_id=coep.id,
            programme_id=coep_cs.id,
            academic_year="2026-27",
            approved_regular_intake=2,
            supernumerary_capacity=2,
            admitted_regular=0,
            admitted_supernumerary=0,
            category_rules={
                "REGULAR": ["OPEN", "OBC", "SC", "ST", "EWS"],
                "SUPERNUMERARY": ["NRI", "OCI", "FN"],
            },
        )
        db.session.add_all([vesit_cap, coep_cap])
        db.session.flush()

        # Seed demo users
        aicte_admin = User(username="AICTE_Admin", full_name="AICTE Admin", role=Role.AICTE_ADMIN.value)
        aicte_admin.set_password("Demo@12345")

        vesit_user = User(username="VESIT", full_name="VESIT Admin", role=Role.INSTITUTION_ADMIN.value, institution_id=vesit.id)
        vesit_user.set_password("Demo@12345")

        coep_user = User(username="COEP", full_name="COEP Admin", role=Role.INSTITUTION_ADMIN.value, institution_id=coep.id)
        coep_user.set_password("Demo@12345")

        db.session.add_all([aicte_admin, vesit_user, coep_user])

        # Seed system policies
        p_regex = SystemPolicy(key="apaar_regex", value="^\\d{12}$", updated_by="SYSTEM")
        p_thresholds = SystemPolicy(
            key="capacity_warning_thresholds",
            value={"warn_80": 80.0, "warn_90": 90.0, "critical_100": 100.0},
            updated_by="SYSTEM",
        )
        db.session.add_all([p_regex, p_thresholds])
        db.session.commit()

        return {
            "vesit_id": vesit.id,
            "coep_id": coep.id,
            "vesit_it_id": vesit_it.id,
            "coep_cs_id": coep_cs.id,
        }


def login(client, username, password="Demo@12345"):
    res = client.post("/api/auth/login", json={"username": username, "password": password})
    assert res.status_code == 200
    return res


# ---------------------------------------------------------------------------
# Tests: GET /api/seats/summary and /api/seats/ledger
# ---------------------------------------------------------------------------

def test_seats_summary_scope_and_filters(client, a10_fixture):
    """AICTE sees all institutions; Institution Admin only sees own institution."""
    # AICTE admin sees both
    login(client, "AICTE_Admin")
    res = client.get("/api/seats/summary?academic_year=2026-27")
    assert res.status_code == 200
    body = res.get_json()
    assert body["ok"] is True
    assert len(body["data"]) == 2

    # Filter by institution
    res_v = client.get(f"/api/seats/summary?institution_id={a10_fixture['vesit_id']}")
    assert res_v.status_code == 200
    assert len(res_v.get_json()["data"]) == 1
    assert res_v.get_json()["data"][0]["institution_code"] == "VESIT"

    # VESIT admin sees only VESIT
    login(client, "VESIT")
    res_inst = client.get("/api/seats/summary?academic_year=2026-27")
    assert res_inst.status_code == 200
    inst_body = res_inst.get_json()
    assert len(inst_body["data"]) == 1
    assert inst_body["data"][0]["institution_id"] == a10_fixture["vesit_id"]

    # Even if VESIT asks for COEP ID, it is blocked with 403 FORBIDDEN
    res_cross = client.get(f"/api/seats/summary?institution_id={a10_fixture['coep_id']}")
    assert res_cross.status_code == 403


def test_seats_ledger_detailed(client, a10_fixture):
    """GET /api/seats/ledger returns breakdown and utilisation numbers."""
    login(client, "VESIT")
    url = f"/api/seats/ledger?institution_id={a10_fixture['vesit_id']}&programme_id={a10_fixture['vesit_it_id']}&academic_year=2026-27"
    res = client.get(url)
    assert res.status_code == 200
    body = res.get_json()
    assert body["ok"] is True
    data = body["data"]
    assert data["approved_regular"] == 120
    assert data["supernumerary_capacity"] == 10
    assert data["regular_admitted"] == 0
    assert data["regular_remaining"] == 120
    assert isinstance(data["category_breakdown"], dict)


# ---------------------------------------------------------------------------
# Tests: POST /api/admissions/precheck
# ---------------------------------------------------------------------------

def test_admissions_precheck_allowed(client, a10_fixture):
    """Precheck returns allowed=True with summary_after, writes no student or event."""
    login(client, "VESIT")
    payload = {
        "institution_id": a10_fixture["vesit_id"],
        "programme_id": a10_fixture["vesit_it_id"],
        "academic_year": "2026-27",
        "institution_student_id": "V-2026-001",
        "full_name": "Rohan Gupta",
        "apaar_ref": "987654321099",
        "category": "OPEN",
        "classification": "REGULAR",
    }
    res = client.post("/api/admissions/precheck", json=payload)
    assert res.status_code == 200
    body = res.get_json()
    assert body["ok"] is True
    assert body["data"]["allowed"] is True
    assert body["data"]["summary_after"]["regular_admitted"] == 1
    assert body["data"]["summary_after"]["regular_remaining"] == 119

    # Verify no student record or admission event was created
    with client.application.app_context():
        count_s = StudentRecord.query.filter_by(apaar_ref="987654321099").count()
        assert count_s == 0
        count_e = AdmissionEvent.query.count()
        assert count_e == 0
        # No rejected audit log written for allowed precheck
        audit = AuditLog.query.filter_by(action="ADMISSION_PRECHECK_REJECTED").count()
        assert audit == 0


def test_admissions_precheck_blocked_and_audited(client, a10_fixture):
    """Precheck blocked for ineligible category writes an audit log."""
    login(client, "VESIT")
    # NRI in REGULAR is not eligible
    payload = {
        "institution_id": a10_fixture["vesit_id"],
        "programme_id": a10_fixture["vesit_it_id"],
        "academic_year": "2026-27",
        "institution_student_id": "V-2026-002",
        "full_name": "Foreign Candidate",
        "apaar_ref": "987654321098",
        "category": "NRI",
        "classification": "REGULAR",
    }
    res = client.post("/api/admissions/precheck", json=payload)
    assert res.status_code == 200
    body = res.get_json()
    assert body["ok"] is True
    assert body["data"]["allowed"] is False
    assert body["data"]["reason_code"] == "CATEGORY_NOT_ELIGIBLE"

    # Verify audit log was written for the rejection
    with client.application.app_context():
        audit = AuditLog.query.filter_by(action="ADMISSION_PRECHECK_REJECTED").first()
        assert audit is not None
        assert audit.details["reason_code"] == "CATEGORY_NOT_ELIGIBLE"


# ---------------------------------------------------------------------------
# Tests: POST /api/admissions (Commit & Enforcement)
# ---------------------------------------------------------------------------

def test_commit_admission_success(client, a10_fixture):
    """Successful admission returns 201 with student and event records."""
    login(client, "VESIT")
    payload = {
        "institution_id": a10_fixture["vesit_id"],
        "programme_id": a10_fixture["vesit_it_id"],
        "academic_year": "2026-27",
        "institution_student_id": "V-2026-101",
        "full_name": "Pooja Hegde",
        "apaar_ref": "987654321101",
        "category": "OPEN",
        "classification": "REGULAR",
        "admission_date": "2026-07-15",
    }
    res = client.post("/api/admissions", json=payload)
    assert res.status_code == 201
    body = res.get_json()
    assert body["ok"] is True
    assert body["data"]["student"]["apaar_ref"] == "XXXXXXXX1101"
    assert body["data"]["event"]["result"] == "ACCEPTED"

    # Verify persisted in DB and admitted counter incremented
    with client.application.app_context():
        cap = IntakeCapacity.query.filter_by(
            institution_id=a10_fixture["vesit_id"],
            programme_id=a10_fixture["vesit_it_id"],
            academic_year="2026-27",
        ).first()
        assert cap.admitted_regular == 1

        event = AdmissionEvent.query.filter_by(student_id=body["data"]["student"]["id"]).first()
        assert event is not None
        assert event.result == AdmissionEventResult.ACCEPTED.value

        audit = AuditLog.query.filter_by(action="ADMISSION_ACCEPTED").first()
        assert audit is not None


def test_commit_admission_cross_institution_forbidden(client, a10_fixture):
    """COEP admin attempting to admit into VESIT returns 403 FORBIDDEN."""
    login(client, "COEP")
    payload = {
        "institution_id": a10_fixture["vesit_id"],  # Trying to admit into VESIT
        "programme_id": a10_fixture["vesit_it_id"],
        "academic_year": "2026-27",
        "institution_student_id": "V-2026-HACK",
        "full_name": "Unauthorized Attempt",
        "apaar_ref": "987654321999",
        "category": "OPEN",
        "classification": "REGULAR",
    }
    res = client.post("/api/admissions", json=payload)
    assert res.status_code == 403
    body = res.get_json()
    assert body["ok"] is False
    assert body["error"]["code"] == "FORBIDDEN"


def test_commit_admission_capacity_exceeded_and_event_recorded(client, a10_fixture):
    """Admissions beyond approved capacity return 409 CAPACITY_EXCEEDED and record REJECTED event."""
    login(client, "COEP")
    # COEP CS approved regular intake is 2
    # 1st student -> OK
    s1 = {
        "institution_id": a10_fixture["coep_id"],
        "programme_id": a10_fixture["coep_cs_id"],
        "academic_year": "2026-27",
        "institution_student_id": "C-2026-001",
        "full_name": "Student One",
        "apaar_ref": "987654320001",
        "category": "OPEN",
        "classification": "REGULAR",
    }
    assert client.post("/api/admissions", json=s1).status_code == 201

    # 2nd student -> OK (reaches 2/2)
    s2 = {
        "institution_id": a10_fixture["coep_id"],
        "programme_id": a10_fixture["coep_cs_id"],
        "academic_year": "2026-27",
        "institution_student_id": "C-2026-002",
        "full_name": "Student Two",
        "apaar_ref": "987654320002",
        "category": "OBC",
        "classification": "REGULAR",
    }
    assert client.post("/api/admissions", json=s2).status_code == 201

    # 3rd student -> 409 CAPACITY_EXCEEDED
    s3 = {
        "institution_id": a10_fixture["coep_id"],
        "programme_id": a10_fixture["coep_cs_id"],
        "academic_year": "2026-27",
        "institution_student_id": "C-2026-003",
        "full_name": "Student Three",
        "apaar_ref": "987654320003",
        "category": "OPEN",
        "classification": "REGULAR",
    }
    res3 = client.post("/api/admissions", json=s3)
    assert res3.status_code == 409
    body3 = res3.get_json()
    assert body3["ok"] is False
    assert body3["error"]["code"] == "CAPACITY_EXCEEDED"
    msg = body3["error"]["message"].lower()
    assert "approved regular intake is 2" in msg or "blocked" in msg

    # Verify REJECTED admission event was recorded in DB
    with client.application.app_context():
        event = AdmissionEvent.query.filter_by(
            institution_id=a10_fixture["coep_id"],
            result=AdmissionEventResult.REJECTED.value,
        ).first()
        assert event is not None
        assert event.result == AdmissionEventResult.REJECTED.value
        assert event.reason_code == "CAPACITY_EXCEEDED"

        # Verify audit log was recorded
        audit = AuditLog.query.filter_by(action="ADMISSION_REJECTED", entity_id=event.id).first()
        assert audit is not None


def test_commit_admission_duplicate_blocked(client, a10_fixture):
    """Attempting duplicate institutional student ID or APAAR returns 409 DUPLICATE_ADMISSION."""
    login(client, "VESIT")
    payload1 = {
        "institution_id": a10_fixture["vesit_id"],
        "programme_id": a10_fixture["vesit_it_id"],
        "academic_year": "2026-27",
        "institution_student_id": "V-2026-DUP",
        "full_name": "First Student",
        "apaar_ref": "987654321901",
        "category": "OPEN",
        "classification": "REGULAR",
    }
    assert client.post("/api/admissions", json=payload1).status_code == 201

    # Attempt same institutional ID with different APAAR
    payload2 = {
        "institution_id": a10_fixture["vesit_id"],
        "programme_id": a10_fixture["vesit_it_id"],
        "academic_year": "2026-27",
        "institution_student_id": "V-2026-DUP",
        "full_name": "Duplicate Student",
        "apaar_ref": "987654321902",
        "category": "OPEN",
        "classification": "REGULAR",
    }
    res2 = client.post("/api/admissions", json=payload2)
    assert res2.status_code == 409
    body2 = res2.get_json()
    assert body2["ok"] is False
    assert body2["error"]["code"] == "DUPLICATE_ADMISSION"


def test_commit_admission_category_not_eligible(client, a10_fixture):
    """Attempting category not allowed for classification returns 409 CATEGORY_NOT_ELIGIBLE."""
    login(client, "VESIT")
    payload = {
        "institution_id": a10_fixture["vesit_id"],
        "programme_id": a10_fixture["vesit_it_id"],
        "academic_year": "2026-27",
        "institution_student_id": "V-2026-NRI",
        "full_name": "NRI Candidate",
        "apaar_ref": "987654321905",
        "category": "NRI",
        "classification": "REGULAR",  # Ineligible
    }
    res = client.post("/api/admissions", json=payload)
    assert res.status_code == 409
    body = res.get_json()
    assert body["ok"] is False
    assert body["error"]["code"] == "CATEGORY_NOT_ELIGIBLE"


# ---------------------------------------------------------------------------
# Tests: GET /api/admissions/events
# ---------------------------------------------------------------------------

def test_admission_events_list_and_filters(client, a10_fixture):
    """GET /api/admissions/events returns paginated list including rejected attempts."""
    login(client, "COEP")
    # Admit 1, reject 1
    client.post("/api/admissions", json={
        "institution_id": a10_fixture["coep_id"],
        "programme_id": a10_fixture["coep_cs_id"],
        "academic_year": "2026-27",
        "institution_student_id": "C-EV-001",
        "full_name": "Ev Student One",
        "apaar_ref": "987654320011",
        "category": "OPEN",
        "classification": "REGULAR",
    })
    client.post("/api/admissions", json={
        "institution_id": a10_fixture["coep_id"],
        "programme_id": a10_fixture["coep_cs_id"],
        "academic_year": "2026-27",
        "institution_student_id": "C-EV-002",
        "full_name": "Ev Student Two",
        "apaar_ref": "987654320012",
        "category": "NRI",
        "classification": "REGULAR",  # rejected
    })

    # Query events
    res = client.get("/api/admissions/events")
    assert res.status_code == 200
    body = res.get_json()
    assert body["ok"] is True
    assert "data" in body
    assert "meta" in body
    assert len(body["data"]) >= 2

    # Check that rejected attempt is present
    rejected = [e for e in body["data"] if e["result"] == "REJECTED"]
    assert len(rejected) >= 1
    assert rejected[0]["reason_code"] == "CATEGORY_NOT_ELIGIBLE"

    # Filter by result=REJECTED
    res_rej = client.get("/api/admissions/events?result=REJECTED")
    assert res_rej.status_code == 200
    for e in res_rej.get_json()["data"]:
        assert e["result"] == "REJECTED"


# ---------------------------------------------------------------------------
# Tests: POST /api/students Integration with Seat Ledger
# ---------------------------------------------------------------------------

def test_students_manual_entry_respects_seat_ledger(client, a10_fixture):
    """Manual student creation via POST /api/students routes through seat ledger and rejects on capacity limit."""
    login(client, "COEP")
    # COEP CS capacity is 2
    # Add 1st student via POST /api/students
    payload1 = {
        "institution_id": a10_fixture["coep_id"],
        "programme_id": a10_fixture["coep_cs_id"],
        "academic_year": "2026-27",
        "institution_student_id": "C-MAN-001",
        "full_name": "Manual Student One",
        "apaar_ref": "987654320021",
        "admission_date": "2026-07-10",
        "admission_category": "OPEN",
        "classification": "REGULAR",
    }
    res1 = client.post("/api/students", json=payload1)
    assert res1.status_code == 201

    # Add 2nd student
    payload2 = {
        "institution_id": a10_fixture["coep_id"],
        "programme_id": a10_fixture["coep_cs_id"],
        "academic_year": "2026-27",
        "institution_student_id": "C-MAN-002",
        "full_name": "Manual Student Two",
        "apaar_ref": "987654320022",
        "admission_date": "2026-07-11",
        "admission_category": "OBC",
        "classification": "REGULAR",
    }
    res2 = client.post("/api/students", json=payload2)
    assert res2.status_code == 201

    # Add 3rd student -> Capacity exceeded 409
    payload3 = {
        "institution_id": a10_fixture["coep_id"],
        "programme_id": a10_fixture["coep_cs_id"],
        "academic_year": "2026-27",
        "institution_student_id": "C-MAN-003",
        "full_name": "Manual Student Three",
        "apaar_ref": "987654320023",
        "admission_date": "2026-07-12",
        "admission_category": "OPEN",
        "classification": "REGULAR",
    }
    res3 = client.post("/api/students", json=payload3)
    assert res3.status_code == 409
    body3 = res3.get_json()
    assert body3["ok"] is False
    assert body3["error"]["code"] == "CAPACITY_EXCEEDED"
