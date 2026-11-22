"""
Comprehensive Integration & Concurrency Tests for Complete Doctor Workflow.

Tests:
  1. Simultaneous atomic claims by two doctors (concurrency race condition)
  2. Expired locks enabling clean takeover by another doctor
  3. Network interruptions & idempotent lock refresh by the same doctor
  4. Clinical case reassignment with mandatory rationale & audit tracking
  5. Prescription revisions preserving clinical history and updating active orders
  6. Duplicate submission detection and idempotency guardrails
  7. Cross-doctor action rejection on actively locked cases (409 Conflict)
"""
import time
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.auth import UserRole, create_token
from app.db import (
    save_consultation,
    get_consultation,
    get_assignment_for_consultation,
    save_assignment,
    _MEM_ASSIGNMENTS,
)
from app.telehealth.clinical_actions import (
    DoctorActionType,
    get_clinical_action_manager,
)

client = TestClient(app)


@pytest.fixture
def doctor_alpha_token():
    return create_token(
        user_id="dr.rajan@hospital.in",
        role=UserRole.DOCTOR,
        doctor_reg_no="TNMC-48291",
        is_verified_doctor=True,
    )


@pytest.fixture
def doctor_beta_token():
    return create_token(
        user_id="dr.priya@hospital.in",
        role=UserRole.DOCTOR,
        doctor_reg_no="TNMC-54321",
        is_verified_doctor=True,
    )


@pytest.fixture
def admin_token():
    return create_token(
        user_id="admin_chief_001",
        role=UserRole.ADMIN,
    )


@pytest.fixture
def test_consultation():
    cid = f"c_test_doc_wf_{int(time.time() * 1000)}"
    save_consultation(cid, {
        "id": cid,
        "urgency_tier": "doctor_today",
        "status": "triage",
        "chief_complaint": "Acute persistent cough and shortness of breath",
        "patient_language": "ta-IN",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    _MEM_ASSIGNMENTS.pop(cid, None)
    return cid


# -----------------------------------------------------------------------------
# 1. Simultaneous Atomic Claims by Two Doctors (Race Condition Concurrency)
# -----------------------------------------------------------------------------
def test_simultaneous_claims_enforce_atomic_locking(test_consultation, doctor_alpha_token, doctor_beta_token):
    """
    Two doctors attempt to claim the exact same consultation simultaneously.
    The backend MUST enforce locking atomically: exactly one succeeds (200),
    and the other is rejected with 409 Conflict.
    """
    results = []

    def attempt_claim(token: str, doctor_name: str):
        resp = client.post(
            f"/api/v1/consultations/{test_consultation}/claim",
            headers={"Authorization": f"Bearer {token}"},
            json={"lock_ttl_minutes": 15},
        )
        return doctor_name, resp.status_code, resp.json()

    with ThreadPoolExecutor(max_workers=2) as executor:
        f_alpha = executor.submit(attempt_claim, doctor_alpha_token, "Doctor Alpha")
        f_beta = executor.submit(attempt_claim, doctor_beta_token, "Doctor Beta")
        results = [f_alpha.result(), f_beta.result()]

    status_codes = [r[1] for r in results]
    assert 200 in status_codes, f"One doctor must acquire the lock. Got: {status_codes}"
    assert 409 in status_codes, f"The competing doctor must receive 409 Conflict. Got: {status_codes}"

    # Exactly 1 success and 1 conflict
    success_count = sum(1 for s in status_codes if s == 200)
    conflict_count = sum(1 for s in status_codes if s == 409)
    assert success_count == 1
    assert conflict_count == 1

    # Verify winning claimant holds the assignment
    winner = next(r for r in results if r[1] == 200)
    assignment = get_assignment_for_consultation(test_consultation)
    assert assignment is not None
    assert assignment["status"] == "claimed"
    expected_doc_id = "dr.rajan@hospital.in" if winner[0] == "Doctor Alpha" else "dr.priya@hospital.in"
    assert assignment["doctor_id"] == expected_doc_id


# -----------------------------------------------------------------------------
# 2. Expired Locks
# -----------------------------------------------------------------------------
def test_expired_lock_allows_clean_takeover(test_consultation, doctor_alpha_token, doctor_beta_token):
    """
    When Doctor Alpha's claim lock expires, Doctor Beta can claim the consultation cleanly.
    """
    # 1. Doctor Alpha claims with 0 TTL (or expired past time)
    res_claim = client.post(
        f"/api/v1/consultations/{test_consultation}/claim",
        headers={"Authorization": f"Bearer {doctor_alpha_token}"},
        json={"lock_ttl_minutes": 15},
    )
    assert res_claim.status_code == 200

    # Simulate lock expiration 5 minutes ago
    assignment = get_assignment_for_consultation(test_consultation)
    past_time = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    assignment["lock_expires_at"] = past_time

    # 2. Doctor Beta claims the expired case
    res_beta = client.post(
        f"/api/v1/consultations/{test_consultation}/claim",
        headers={"Authorization": f"Bearer {doctor_beta_token}"},
        json={"lock_ttl_minutes": 15},
    )
    assert res_beta.status_code == 200
    data = res_beta.json()
    assert data["success"] is True
    assert data["assignment"]["doctor_id"] == "dr.priya@hospital.in"

    # Consultation reflects Doctor Beta
    updated_consultation = get_consultation(test_consultation)
    assert updated_consultation["assigned_doctor_id"] == "dr.priya@hospital.in"


# -----------------------------------------------------------------------------
# 3. Network Interruptions / Idempotent Lock Refresh
# -----------------------------------------------------------------------------
def test_network_interruption_idempotent_reclaim(test_consultation, doctor_alpha_token):
    """
    If a network glitch or page reload occurs, the same doctor can re-send
    claim requests without 409 conflict, refreshing their own active lock.
    """
    # First claim
    res1 = client.post(
        f"/api/v1/consultations/{test_consultation}/claim",
        headers={"Authorization": f"Bearer {doctor_alpha_token}"},
        json={"lock_ttl_minutes": 15},
    )
    assert res1.status_code == 200

    # Network interruption / retry with same doctor credentials
    res2 = client.post(
        f"/api/v1/consultations/{test_consultation}/claim",
        headers={"Authorization": f"Bearer {doctor_alpha_token}"},
        json={"lock_ttl_minutes": 20},
    )
    assert res2.status_code == 200
    assert res2.json()["success"] is True
    assert res2.json()["assignment"]["doctor_id"] == "dr.rajan@hospital.in"


# -----------------------------------------------------------------------------
# 4. Reassignment Workflow
# -----------------------------------------------------------------------------
def test_consultation_reassignment_with_audit(test_consultation, doctor_alpha_token, admin_token):
    """
    Tests clinical case reassignment with mandatory clinical reason,
    verifying rejection on insufficient rationale and success on valid justification.
    """
    # Reassignment fails if rationale is too short
    res_short = client.post(
        f"/api/v1/consultations/{test_consultation}/reassign",
        headers={"Authorization": f"Bearer {doctor_alpha_token}"},
        json={"target_doctor_id": "dr.priya@hospital.in", "reason": "bad"},
    )
    assert res_short.status_code in (400, 422)

    # Valid reassignment to Doctor Beta
    clinical_reason = "Patient exhibits signs of bronchial hyperreactivity; transferring to Pulmonologist."
    res_ok = client.post(
        f"/api/v1/consultations/{test_consultation}/reassign",
        headers={"Authorization": f"Bearer {doctor_alpha_token}"},
        json={
            "target_doctor_id": "dr.priya@hospital.in",
            "target_department": "CARDIOLOGY",
            "reason": clinical_reason,
        },
    )
    assert res_ok.status_code == 200
    assert res_ok.json()["success"] is True

    # Check assignment updated
    consultation = get_consultation(test_consultation)
    assert consultation["assigned_doctor_id"] == "dr.priya@hospital.in"
    assert consultation["department_id"] == "CARDIOLOGY"


# -----------------------------------------------------------------------------
# 5. Prescription Revisions
# -----------------------------------------------------------------------------
def test_prescription_revision_workflow(test_consultation, doctor_alpha_token):
    """
    Doctor claims case, issues an initial prescription, and then submits a
    revised prescription. Action history must maintain both records while
    the consultation state reflects the latest prescription.
    """
    # 1. Doctor Alpha claims case
    client.post(
        f"/api/v1/consultations/{test_consultation}/claim",
        headers={"Authorization": f"Bearer {doctor_alpha_token}"},
        json={"lock_ttl_minutes": 15},
    )

    # 2. Initial Prescription (Amoxicillin 500mg)
    initial_action = {
        "consultation_id": test_consultation,
        "action_type": "PRESCRIBE_MEDICATION",
        "clinical_notes": "Initial prescription for bacterial respiratory infection.",
        "prescriptions": [
            {
                "medication_name": "Amoxicillin",
                "dosage": "500mg",
                "frequency": "TDS (Thrice daily)",
                "duration": "5 days",
                "instructions": "Take after meals with water.",
            }
        ],
    }
    res_init = client.post(
        "/api/v1/consultations/action",
        headers={"Authorization": f"Bearer {doctor_alpha_token}"},
        json=initial_action,
    )
    assert res_init.status_code == 200
    act1_id = res_init.json()["action_id"]

    # 3. Prescription Revision (Upgraded to Augmentin 625mg + Montelukast 10mg)
    time.sleep(0.05)  # slight timestamp difference
    revised_action = {
        "consultation_id": test_consultation,
        "action_type": "PRESCRIBE_MEDICATION",
        "clinical_notes": "Revision: Patient exhibits persistent wheeze. Switching to Augmentin and adding bronchodilator.",
        "prescriptions": [
            {
                "medication_name": "Amoxicillin + Clavulanate",
                "dosage": "625mg",
                "frequency": "BD (Twice daily)",
                "duration": "7 days",
                "instructions": "Take immediately after breakfast and dinner.",
            },
            {
                "medication_name": "Montelukast",
                "dosage": "10mg",
                "frequency": "OD (Once daily at night)",
                "duration": "10 days",
                "instructions": "Bedtime dose.",
            },
        ],
    }
    res_rev = client.post(
        "/api/v1/consultations/action",
        headers={"Authorization": f"Bearer {doctor_alpha_token}"},
        json=revised_action,
    )
    assert res_rev.status_code == 200
    act2_id = res_rev.json()["action_id"]
    assert act1_id != act2_id

    # 4. Verify history retains both actions
    res_history = client.get(
        f"/api/v1/consultations/{test_consultation}/actions",
        headers={"Authorization": f"Bearer {doctor_alpha_token}"},
    )
    assert res_history.status_code == 200
    history = res_history.json()
    assert len(history) >= 2
    action_ids = [h["action_id"] for h in history]
    assert act1_id in action_ids
    assert act2_id in action_ids

    # 5. Verify consultation reflects latest revised prescriptions (2 items)
    consultation = get_consultation(test_consultation)
    assert len(consultation["prescriptions"]) == 2
    assert consultation["prescriptions"][0]["medication_name"] == "Amoxicillin + Clavulanate"
    assert consultation["prescriptions"][1]["medication_name"] == "Montelukast"


# -----------------------------------------------------------------------------
# 6. Duplicate Submission Detection (Idempotency)
# -----------------------------------------------------------------------------
def test_duplicate_submission_detection(test_consultation, doctor_alpha_token):
    """
    Submitting the exact same clinical action within a 10s window (e.g. rapid double-click
    or network retry) returns the existing record rather than duplicating it.
    """
    client.post(
        f"/api/v1/consultations/{test_consultation}/claim",
        headers={"Authorization": f"Bearer {doctor_alpha_token}"},
        json={"lock_ttl_minutes": 15},
    )

    action_payload = {
        "consultation_id": test_consultation,
        "action_type": "CONFIRM_TRIAGE",
        "clinical_notes": "Triage verified: Patient stable for observation.",
        "prescriptions": [],
    }

    # First submission
    res1 = client.post(
        "/api/v1/consultations/action",
        headers={"Authorization": f"Bearer {doctor_alpha_token}"},
        json=action_payload,
    )
    assert res1.status_code == 200
    first_record = res1.json()

    # Immediate duplicate submission
    res2 = client.post(
        "/api/v1/consultations/action",
        headers={"Authorization": f"Bearer {doctor_alpha_token}"},
        json=action_payload,
    )
    assert res2.status_code == 200
    second_record = res2.json()

    # Must return identical action_id without adding duplicate entries
    assert first_record["action_id"] == second_record["action_id"]


# -----------------------------------------------------------------------------
# 7. Action Blocked on Other Doctor's Locked Case
# -----------------------------------------------------------------------------
def test_action_blocked_when_locked_by_other_doctor(test_consultation, doctor_alpha_token, doctor_beta_token):
    """
    Doctor Alpha holds active claim lock.
    Doctor Beta attempts to prescribe medication or confirm triage on Doctor Alpha's case.
    The backend MUST reject Doctor Beta with 409 Conflict.
    """
    # Doctor Alpha claims case
    res_claim = client.post(
        f"/api/v1/consultations/{test_consultation}/claim",
        headers={"Authorization": f"Bearer {doctor_alpha_token}"},
        json={"lock_ttl_minutes": 15},
    )
    assert res_claim.status_code == 200

    # Doctor Beta tries to perform action on Alpha's locked case
    action_payload = {
        "consultation_id": test_consultation,
        "action_type": "CONFIRM_TRIAGE",
        "clinical_notes": "Attempting unpermitted review.",
        "prescriptions": [],
    }
    res_beta_action = client.post(
        "/api/v1/consultations/action",
        headers={"Authorization": f"Bearer {doctor_beta_token}"},
        json=action_payload,
    )
    assert res_beta_action.status_code == 409
    assert "currently claimed and locked" in res_beta_action.json()["detail"]
