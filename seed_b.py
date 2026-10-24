"""Idempotent, synthetic Member B demos. No new login accounts or institutions."""
import base64
from datetime import timedelta
import json

from flask import current_app
from flask_login import login_user
from sqlalchemy import select

from extensions import db
from models import AdmissionEvent, Credential, RiskCase, StudentRecord, User
from services import credential_service as service, identity_resolution, key_store, risk_engine, sharing_service


def make_tampered_demo(row: Credential, actor: User) -> None:
    """DEMO ONLY: deliberately alter stored claims after signing; never re-sign this edit."""
    if not current_app.config.get("SHOW_DEMO_ACCOUNTS"):
        raise RuntimeError("Tampering demo is disabled outside the development demo.")
    payload = json.loads(row.payload_json)
    payload["programme"] = "DEMO ALTERED AFTER SIGNING"
    row.payload_json = service.canonicalize(payload).decode()
    service._audit(actor, "DEMO_CREDENTIAL_TAMPERED", row.id, {"synthetic": True}, row.institution_id)


def seed_demo_credentials() -> list[str]:
    """Prepare four real signed examples, retaining their actual verification states."""
    if not current_app.config.get("SHOW_DEMO_ACCOUNTS"):
        return []
    actor = db.session.execute(select(User).where(User.username == "AICTE_Admin")).scalar_one_or_none()
    students = db.session.execute(select(StudentRecord).where(StudentRecord.is_synthetic == True,
        StudentRecord.validation_status == "VALID", StudentRecord.enrolment_status.in_(["ACTIVE", "GRADUATED"]))
        .order_by(StudentRecord.id).limit(4)).scalars().all()
    if actor is None or not students:
        raise RuntimeError("Seed the official accounts and eligible synthetic students first.")
    refs = [f"CRED-DEMO0{index}" for index in range(1, 5)]
    for index, reference in enumerate(refs):
        if db.session.execute(select(Credential.id).where(Credential.reference == reference)).first():
            continue
        student = students[index % len(students)]
        row = service.issue_credential(student.id, str(service.utcnow().year - index), actor=actor, acknowledge_warnings=True)
        payload = json.loads(row.payload_json)
        row.reference = reference
        payload["credential_id"] = reference
        if index == 2:
            row.issue_date = (service.utcnow() - timedelta(days=730)).date()
            row.expires_at = service.utcnow() - timedelta(days=1)
            payload["issue_date"], payload["expires_at"] = row.issue_date.isoformat(), service.iso(row.expires_at)
        encoded = service.canonicalize(payload)
        row.payload_json = encoded.decode()
        row.signature = base64.b64encode(key_store.load_private_key(row.key_id).sign(encoded)).decode()
        service._audit(actor, "DEMO_CREDENTIAL_PREPARED", row.id, {"reference": reference, "synthetic": True}, row.institution_id)
        if index == 1:
            service.revoke_credential(row.id, "Synthetic demonstration revocation", actor)
        elif index == 3:
            make_tampered_demo(row, actor)
    return refs


def seed_prove_and_detect(database) -> None:
    """Build real Detect cases and synthetic Prove demos, without extra users."""
    if not current_app.config.get("SHOW_DEMO_ACCOUNTS"):
        return
    result = identity_resolution.scan()
    risk_engine.evaluate_all(result)
    rejected = db.session.execute(select(AdmissionEvent).where(
        AdmissionEvent.result == "REJECTED", AdmissionEvent.reason_code == "CAPACITY_EXCEEDED")).scalars().all()
    for event in rejected:
        risk_engine.record_excess_intake_event(event)
    bands = {case.severity for case in db.session.execute(select(RiskCase)).scalars()}
    missing = {"LOW", "MEDIUM", "HIGH", "CRITICAL"} - bands
    if missing:
        current_app.logger.warning("Member A seed scenarios need adjustment: missing %s risk band(s).", ", ".join(sorted(missing)))
    actor = db.session.execute(select(User).where(User.username == "AICTE_Admin")).scalar_one()
    review_case = db.session.execute(select(RiskCase).where(RiskCase.severity == "LOW", RiskCase.status == "OPEN")
        .order_by(RiskCase.reference)).scalars().first()
    if review_case:
        # The development seed creates a synthetic administrator review action
        # through the same authenticated service used by the live workflow.
        with current_app.test_request_context("/internal/seed-b"):
            login_user(actor)
            risk_engine.act_on_case(review_case.id, "UNDER_REVIEW", "Synthetic demonstration review")
    refs = seed_demo_credentials()
    credential = db.session.execute(select(Credential).where(Credential.reference == "CRED-DEMO01")).scalar_one()
    token = sharing_service.seed_demo_sessions(credential, actor)
    print(f"Synthetic Detect/Prove summary: {len(result.events)} identity events, "
          f"{db.session.query(RiskCase).count()} risk cases, {len(refs)} demo credentials, 3 sharing sessions.")
    if missing:
        print(f"Member A seed adjustment needed: missing risk band(s): {', '.join(sorted(missing))}.")
    if token:
        print(f"Demo-only sharing token (shown once; copy now): {token}")
