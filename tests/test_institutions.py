"""
Tests for institutions, programmes, intake capacities, and system policies (Task A5).
Verifies role authorization, institution scoping, capacity protection, and policy validation.
"""
import pytest
from datetime import date
from extensions import db
from models.institution import Institution, Programme, IntakeCapacity
from models.student import StudentRecord
from models.user import User
from models.policy import SystemPolicy
from models.audit import AuditLog
from models.enums import Role, Classification, AdmissionCategory, EnrolmentStatus, ValidationStatus


@pytest.fixture
def a5_fixture(app):
    """Seed test institutions, programmes, capacities, policies, and users."""
    with app.app_context():
        # Clean up any leftover test data
        db.session.query(StudentRecord).delete()
        db.session.query(IntakeCapacity).delete()
        db.session.query(Programme).delete()
        db.session.query(Institution).delete()
        db.session.query(User).delete()
        db.session.query(SystemPolicy).delete()
        db.session.query(AuditLog).delete()
        db.session.commit()

        # Institutions
        vesit = Institution(code="VESIT", name="VESIT Mumbai", state="Maharashtra", city="Mumbai", status="ACTIVE")
        coep = Institution(code="COEP", name="COEP Pune", state="Maharashtra", city="Pune", status="ACTIVE")
        vjti = Institution(code="VJTI", name="VJTI Mumbai", state="Maharashtra", city="Mumbai", status="INACTIVE")
        db.session.add_all([vesit, coep, vjti])
        db.session.flush()

        # Programmes
        it_prog = Programme(institution_id=vesit.id, code="IT", name="Information Technology", level="UG")
        cs_prog = Programme(institution_id=coep.id, code="CS", name="Computer Science", level="UG")
        db.session.add_all([it_prog, cs_prog])
        db.session.flush()

        # Intake Capacities
        vesit_cap = IntakeCapacity(
            institution_id=vesit.id,
            programme_id=it_prog.id,
            academic_year="2026-27",
            approved_regular_intake=120,
            supernumerary_capacity=10
        )
        db.session.add(vesit_cap)
        db.session.flush()

        # Seed 5 regular students into VESIT IT (to test capacity reduction guard)
        for i in range(5):
            s = StudentRecord(
                institution_id=vesit.id,
                programme_id=it_prog.id,
                institution_student_id=f"V{i:03d}",
                full_name=f"Student {i}",
                normalized_name=f"student {i}",
                academic_year="2026-27",
                admission_date=date.today(),
                admission_category=AdmissionCategory.OPEN.value,
                classification=Classification.REGULAR.value,
                enrolment_status=EnrolmentStatus.ACTIVE.value,
                validation_status=ValidationStatus.VALID.value
            )
            db.session.add(s)

        # Users
        admin = User(username="AICTE_Admin", full_name="AICTE Admin", role=Role.AICTE_ADMIN.value)
        admin.set_password("Demo@12345")

        vesit_user = User(username="VESIT", full_name="VESIT Admin", role=Role.INSTITUTION_ADMIN.value, institution_id=vesit.id)
        vesit_user.set_password("Demo@12345")

        coep_user = User(username="COEP", full_name="COEP Admin", role=Role.INSTITUTION_ADMIN.value, institution_id=coep.id)
        coep_user.set_password("Demo@12345")

        auditor = User(username="AUDITOR", full_name="Auditor", role=Role.AUDITOR.value)
        auditor.set_password("Demo@12345")

        db.session.add_all([admin, vesit_user, coep_user, auditor])

        # Policies
        p1 = SystemPolicy(key="apaar_regex", value="^\\d{12}$", updated_by="SYSTEM")
        p2 = SystemPolicy(key="capacity_warning_thresholds", value=[80, 90, 100], updated_by="SYSTEM")
        p3 = SystemPolicy(key="risk_weights", value={"DUPLICATE_APAAR": 40, "SHARED_CONTACT": 10}, updated_by="SYSTEM")
        p4 = SystemPolicy(key="sharing_allowed_expiry_hours", value=[24, 72, 168], updated_by="SYSTEM")
        db.session.add_all([p1, p2, p3, p4])

        db.session.commit()

        return {
            "vesit_id": vesit.id,
            "coep_id": coep.id,
            "vjti_id": vjti.id,
            "it_prog_id": it_prog.id,
            "vesit_cap_id": vesit_cap.id,
        }


def login(client, username, password="Demo@12345"):
    res = client.post("/api/auth/login", json={"username": username, "password": password})
    assert res.status_code == 200
    return res


# ---------------------------------------------------------------------------
# Institution Scoping & Access Control Tests
# ---------------------------------------------------------------------------

def test_list_institutions_scoping(app, client, a5_fixture):
    """AICTE_ADMIN sees all institutions, INSTITUTION_ADMIN sees only own institution."""
    # AICTE Admin sees all 3
    login(client, "AICTE_Admin")
    res = client.get("/api/institutions")
    assert res.status_code == 200
    data = res.get_json()["data"]
    assert len(data) == 3

    # Filter by search q
    res_q = client.get("/api/institutions?q=pune")
    assert res_q.status_code == 200
    assert len(res_q.get_json()["data"]) == 1
    assert res_q.get_json()["data"][0]["code"] == "COEP"

    # Filter by status
    res_status = client.get("/api/institutions?status=INACTIVE")
    assert res_status.status_code == 200
    assert len(res_status.get_json()["data"]) == 1
    assert res_status.get_json()["data"][0]["code"] == "VJTI"

    # Institution Admin sees only own (VESIT)
    login(client, "VESIT")
    res_inst = client.get("/api/institutions")
    assert res_inst.status_code == 200
    data_inst = res_inst.get_json()["data"]
    assert len(data_inst) == 1
    assert data_inst[0]["code"] == "VESIT"


def test_institution_admin_cannot_read_another_institution(app, client, a5_fixture):
    """Institution admin accessing another institution's detail gets 403 Forbidden."""
    login(client, "VESIT")

    # Own institution details -> 200
    res_own = client.get(f"/api/institutions/{a5_fixture['vesit_id']}")
    assert res_own.status_code == 200
    assert res_own.get_json()["data"]["institution"]["code"] == "VESIT"

    # Another institution details -> 403
    res_other = client.get(f"/api/institutions/{a5_fixture['coep_id']}")
    assert res_other.status_code == 403
    assert res_other.get_json()["error"]["code"] == "FORBIDDEN"


def test_institution_admin_cannot_patch_another_institution(app, client, a5_fixture):
    """Institution admin patching another institution gets 403 Forbidden."""
    login(client, "VESIT")

    res = client.patch(
        f"/api/institutions/{a5_fixture['coep_id']}",
        json={"city": "New Pune"}
    )
    assert res.status_code == 403
    assert res.get_json()["error"]["code"] == "FORBIDDEN"


def test_institution_admin_limited_patch_on_own_institution(app, client, a5_fixture):
    """Institution admin can only update allowed fields (city/state) on own institution."""
    login(client, "VESIT")

    # Allowed fields: city
    res_city = client.patch(
        f"/api/institutions/{a5_fixture['vesit_id']}",
        json={"city": "Navi Mumbai"}
    )
    assert res_city.status_code == 200
    assert res_city.get_json()["data"]["city"] == "Navi Mumbai"

    # Disallowed fields: code, name, status -> 403
    res_forbidden = client.patch(
        f"/api/institutions/{a5_fixture['vesit_id']}",
        json={"name": "Hacked Institute Name"}
    )
    assert res_forbidden.status_code == 403
    assert res_forbidden.get_json()["error"]["code"] == "FORBIDDEN"


# ---------------------------------------------------------------------------
# Programmes & Capacities Management Tests
# ---------------------------------------------------------------------------

def test_programmes_management(app, client, a5_fixture):
    """AICTE Admin can create programmes; duplicate codes are rejected; Institution admin is read-only."""
    login(client, "AICTE_Admin")

    # Create programme
    res = client.post(
        f"/api/institutions/{a5_fixture['vesit_id']}/programmes",
        json={"code": "EXTC", "name": "Electronics & Telecom", "level": "UG"}
    )
    assert res.status_code == 201
    assert res.get_json()["data"]["code"] == "EXTC"

    # Duplicate programme code -> 409 Conflict
    res_dup = client.post(
        f"/api/institutions/{a5_fixture['vesit_id']}/programmes",
        json={"code": "EXTC", "name": "Duplicate EXTC", "level": "UG"}
    )
    assert res_dup.status_code == 409
    assert res_dup.get_json()["error"]["code"] == "CONFLICT"

    # Institution admin cannot create programme -> 403
    login(client, "VESIT")
    res_inst = client.post(
        f"/api/institutions/{a5_fixture['vesit_id']}/programmes",
        json={"code": "MECH", "name": "Mechanical", "level": "UG"}
    )
    assert res_inst.status_code == 403


def test_capacity_reduction_guard(app, client, a5_fixture):
    """Approved intake cannot be reduced below current active regular admissions."""
    login(client, "AICTE_Admin")
    cap_id = a5_fixture["vesit_cap_id"]
    inst_id = a5_fixture["vesit_id"]

    # There are 5 regular students in 2026-27 IT. Attempting to reduce intake to 4 -> 409 CONFLICT
    res_conflict = client.patch(
        f"/api/institutions/{inst_id}/capacities/{cap_id}",
        json={"approved_regular_intake": 4}
    )
    assert res_conflict.status_code == 409
    assert res_conflict.get_json()["error"]["code"] == "CONFLICT"
    assert "currently 5 active regular admissions" in res_conflict.get_json()["error"]["message"]

    # Reducing to exactly 5 or increasing to 150 -> 200 OK
    res_ok = client.patch(
        f"/api/institutions/{inst_id}/capacities/{cap_id}",
        json={"approved_regular_intake": 150}
    )
    assert res_ok.status_code == 200
    assert res_ok.get_json()["data"]["approved_regular_intake"] == 150

    # Negative intake -> 422 VALIDATION_ERROR
    res_neg = client.patch(
        f"/api/institutions/{inst_id}/capacities/{cap_id}",
        json={"approved_regular_intake": -10}
    )
    assert res_neg.status_code == 422


# ---------------------------------------------------------------------------
# Policy Validation & Access Control Tests
# ---------------------------------------------------------------------------

def test_institution_admin_cannot_edit_policies(app, client, a5_fixture):
    """Institution admin is forbidden from reading or updating system policies."""
    login(client, "VESIT")

    res_get = client.get("/api/policies")
    assert res_get.status_code == 403

    res_patch = client.patch("/api/policies/apaar_regex", json={"value": "^\\d{10}$"})
    assert res_patch.status_code == 403


def test_invalid_policy_values_rejected(app, client, a5_fixture):
    """Invalid shapes for warning thresholds, risk weights, expiry, and regex are rejected with 422."""
    login(client, "AICTE_Admin")

    # Thresholds: must be ascending and <= 100
    res_desc = client.patch("/api/policies/capacity_warning_thresholds", json={"value": [90, 80]})
    assert res_desc.status_code == 422
    assert res_desc.get_json()["error"]["code"] == "VALIDATION_ERROR"

    res_over100 = client.patch("/api/policies/capacity_warning_thresholds", json={"value": [80, 110]})
    assert res_over100.status_code == 422

    # Risk weights: must be non-negative ints
    res_neg_weight = client.patch("/api/policies/risk_weights", json={"value": {"DUPLICATE_APAAR": -10}})
    assert res_neg_weight.status_code == 422

    # Expiry options: must be subset of {24, 72, 168}
    res_bad_expiry = client.patch("/api/policies/sharing_allowed_expiry_hours", json={"value": [24, 50]})
    assert res_bad_expiry.status_code == 422

    # Regex: invalid syntax
    res_bad_regex = client.patch("/api/policies/apaar_regex", json={"value": "[0-9("})
    assert res_bad_regex.status_code == 422

    # Valid policy updates succeed
    res_valid_regex = client.patch("/api/policies/apaar_regex", json={"value": "^\\d{12}$"})
    assert res_valid_regex.status_code == 200

    res_valid_thresh = client.patch("/api/policies/capacity_warning_thresholds", json={"value": [75, 85, 95]})
    assert res_valid_thresh.status_code == 200


def test_audit_entries_created_for_mutations(app, client, a5_fixture):
    """Mutating institutions, capacities, and policies writes to the AuditLog."""
    login(client, "AICTE_Admin")

    # 1. Update policy
    client.patch("/api/policies/capacity_warning_thresholds", json={"value": [70, 80, 90]})

    # 2. Update institution
    client.patch(f"/api/institutions/{a5_fixture['vesit_id']}", json={"city": "Thane"})

    with app.app_context():
        logs = AuditLog.query.all()
        actions = {l.action for l in logs}
        assert "POLICY_UPDATE" in actions
        assert "INSTITUTION_UPDATE" in actions


# ---------------------------------------------------------------------------
# HTML Page Rendering Tests
# ---------------------------------------------------------------------------

def test_html_pages_render(app, client, a5_fixture):
    """Institutions page loads for all; Policies page only loads for AICTE_ADMIN."""
    login(client, "AICTE_Admin")

    res_inst = client.get("/institutions")
    assert res_inst.status_code == 200
    assert b"Institutions Directory" in res_inst.data

    res_pol = client.get("/policies")
    assert res_pol.status_code == 200
    assert b"System Policies" in res_pol.data

    # Institution admin can load /institutions but is forbidden from /policies
    login(client, "VESIT")
    assert client.get("/institutions").status_code == 200
    assert client.get("/policies").status_code == 403
