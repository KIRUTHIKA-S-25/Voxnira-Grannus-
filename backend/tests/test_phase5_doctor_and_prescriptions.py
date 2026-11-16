"""
Unit and Integration Tests for Phase 5: Doctor and Hospital Workflow, Clinical Review, and Prescriptions.

Covers:
  1. Doctor NMC / State Medical Council registration format and verification enforcement
  2. Unverified doctors and non-clinicians blocked from clinical actions (403 Forbidden)
  3. Atomic consultation queue claiming and 409 Conflict rejection for active locks
  4. Automatic lock expiration after TTL allowing clean reclaim
  5. Lock release returning consultation case to active triage queue
  6. Structured prescription validation (required medication name, dosage, frequency, duration)
  7. Prescription persistence and synchronization to consultation records
  8. Voice prescription back-translation verification and voice thread linkage
"""
import io
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.auth import (
    UserRole,
    create_token,
    validate_doctor_registration,
)
from app.db import (
    save_consultation,
    get_consultation,
    claim_consultation_lock,
    release_consultation_lock,
    save_assignment,
    get_assignment_for_consultation,
)
from app.telehealth.clinical_actions import (
    DoctorActionType,
    DoctorActionRequest,
    PrescriptionItem,
    get_clinical_action_manager,
)
from app.voice_threads import get_voice_thread_manager

client = TestClient(app)

SAMPLE_WAV_HEADER = b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00" + b"\x00" * 120


@pytest.fixture(autouse=True)
def clean_consultation_fixture():
    from app.db import _MEM_ASSIGNMENTS
    _MEM_ASSIGNMENTS.pop("c_phase5_lock_test", None)
    _MEM_ASSIGNMENTS.pop("c_phase5_rx_test", None)

    # Setup standard test consultation
    save_consultation("c_phase5_lock_test", {
        "id": "c_phase5_lock_test",
        "urgency_tier": "doctor_today",
        "status": "triage",
        "chief_complaint": "Persistent productive cough and moderate fever",
        "patient_language": "ta-IN",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    save_consultation("c_phase5_rx_test", {
        "id": "c_phase5_rx_test",
        "urgency_tier": "doctor_soon",
        "status": "triage",
        "chief_complaint": "Mild acute pharyngitis",
        "patient_language": "hi-IN",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })


def test_doctor_registration_validation():
    """Verify NMC and State Medical Council registration format regex validation."""
    valid_regs = [
        "TNMC-48291",
        "MCI-123456",
        "KMC-99112",
        "GMC-39104",
        "123456",
        "DEMO-NMC-GENMED-01",
    ]
    for reg in valid_regs:
        assert validate_doctor_registration(reg) is True, f"Expected {reg} to be valid"

    invalid_regs = [
        "",
        "   ",
        "abc",
        "123",
        "doc",
        "INVALID$$$REG",
    ]
    for reg in invalid_regs:
        assert validate_doctor_registration(reg) is False, f"Expected {reg} to be invalid"


def test_unverified_doctor_blocked_from_clinical_actions():
    """Ensure doctors without verified registration are strictly rejected with 403 Forbidden."""
    unverified_token = create_token(
        user_id="dr_unverified",
        role=UserRole.DOCTOR,
        doctor_reg_no="TNMC-48291",
        is_verified_doctor=False,
    )

    action_payload = {
        "consultation_id": "c_phase5_rx_test",
        "action_type": "CONFIRM_TRIAGE",
        "clinical_notes": "Reviewed symptoms and confirmed moderate urgency.",
    }

    # 1. Action endpoint blocked
    res = client.post(
        "/api/v1/consultations/action",
        headers={"Authorization": f"Bearer {unverified_token}"},
        json=action_payload,
    )
    assert res.status_code == 403
    assert "Unverified doctor" in res.json()["detail"]

    # 2. Claim case endpoint blocked
    res_claim = client.post(
        "/api/v1/consultations/c_phase5_rx_test/claim",
        headers={"Authorization": f"Bearer {unverified_token}"},
        json={},
    )
    assert res_claim.status_code == 403

    # 3. Urgency tier override endpoint blocked
    res_override = client.post(
        "/api/v1/consultations/c_phase5_rx_test/override-tier",
        headers={"Authorization": f"Bearer {unverified_token}"},
        json={"new_tier": "emergency", "reason": "Severe escalation detected."},
    )
    assert res_override.status_code == 403

    # 4. Voice prescription endpoint blocked
    res_voice = client.post(
        "/api/v1/doctor/voice-prescription",
        headers={"Authorization": f"Bearer {unverified_token}"},
        files={"audio": ("audio.wav", SAMPLE_WAV_HEADER, "audio/wav")},
        data={"patient_language": "ta-IN", "consultation_id": "c_phase5_rx_test"},
    )
    assert res_voice.status_code == 403


def test_atomic_queue_locking_conflict():
    """Verify that multiple clinicians cannot claim the same case concurrently (409 Conflict)."""
    doc1_token = create_token(
        user_id="dr_subramaniam",
        role=UserRole.DOCTOR,
        doctor_reg_no="TNMC-77889",
        is_verified_doctor=True,
    )
    doc2_token = create_token(
        user_id="dr_priya",
        role=UserRole.DOCTOR,
        doctor_reg_no="TNMC-54321",
        is_verified_doctor=True,
    )

    # Doctor 1 claims case
    res1 = client.post(
        "/api/v1/consultations/c_phase5_lock_test/claim",
        headers={"Authorization": f"Bearer {doc1_token}"},
        json={"lock_ttl_minutes": 15},
    )
    assert res1.status_code == 200
    assert res1.json()["success"] is True

    # Doctor 2 attempts to claim active lock -> 409 Conflict
    res2 = client.post(
        "/api/v1/consultations/c_phase5_lock_test/claim",
        headers={"Authorization": f"Bearer {doc2_token}"},
        json={"lock_ttl_minutes": 15},
    )
    assert res2.status_code == 409
    assert "already locked" in res2.json()["detail"]


def test_expired_queue_lock_reclaimable():
    """Verify that when a lock expires after TTL, another clinician can cleanly claim it."""
    doc2_token = create_token(
        user_id="dr_priya",
        role=UserRole.DOCTOR,
        doctor_reg_no="TNMC-54321",
        is_verified_doctor=True,
    )

    # Simulate an expired lock by saving an assignment that expired 10 minutes ago
    past_time = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    save_assignment(
        consultation_id="c_phase5_lock_test",
        doctor_id="dr_subramaniam",
        status="claimed",
        locked_by_name="dr_subramaniam",
        lock_ttl_minutes=0,
    )
    assignment = get_assignment_for_consultation("c_phase5_lock_test")
    assignment["lock_expires_at"] = past_time

    # Doctor 2 claims the case whose previous lock has expired
    res = client.post(
        "/api/v1/consultations/c_phase5_lock_test/claim",
        headers={"Authorization": f"Bearer {doc2_token}"},
        json={"lock_ttl_minutes": 15},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["assignment"]["doctor_id"] == "dr_priya"


def test_lock_release_returns_to_triage():
    """Verify that releasing a lock resets consultation status back to triage."""
    doc1_token = create_token(
        user_id="dr_subramaniam",
        role=UserRole.DOCTOR,
        doctor_reg_no="TNMC-77889",
        is_verified_doctor=True,
    )

    # Claim
    res_claim = client.post(
        "/api/v1/consultations/c_phase5_lock_test/claim",
        headers={"Authorization": f"Bearer {doc1_token}"},
        json={"lock_ttl_minutes": 15},
    )
    assert res_claim.status_code == 200
    c_after_claim = get_consultation("c_phase5_lock_test")
    assert c_after_claim["status"] == "in_review"

    # Release
    res_rel = client.post(
        "/api/v1/consultations/c_phase5_lock_test/release",
        headers={"Authorization": f"Bearer {doc1_token}"},
    )
    assert res_rel.status_code == 200

    c_after_release = get_consultation("c_phase5_lock_test")
    assert c_after_release["status"] == "triage"
    assert c_after_release["assigned_doctor_id"] is None


def test_prescription_validation_and_persistence():
    """Verify structured prescription item validation and automatic database persistence."""
    doc_token = create_token(
        user_id="dr_rajan",
        role=UserRole.DOCTOR,
        doctor_reg_no="TNMC-48291",
        state_council="Tamil Nadu Medical Council",
        is_verified_doctor=True,
    )

    # 1. Reject empty prescriptions array when action_type is PRESCRIBE_MEDICATION
    bad_payload_empty = {
        "consultation_id": "c_phase5_rx_test",
        "action_type": "PRESCRIBE_MEDICATION",
        "clinical_notes": "Prescription test with missing items.",
        "prescriptions": [],
    }
    res_bad1 = client.post(
        "/api/v1/consultations/action",
        headers={"Authorization": f"Bearer {doc_token}"},
        json=bad_payload_empty,
    )
    assert res_bad1.status_code == 400
    assert "at least one medication item" in res_bad1.json()["detail"]

    # 2. Reject prescription item with missing required dosage
    bad_payload_fields = {
        "consultation_id": "c_phase5_rx_test",
        "action_type": "PRESCRIBE_MEDICATION",
        "clinical_notes": "Prescription test with missing dosage.",
        "prescriptions": [
            {
                "medication_name": "Amoxicillin",
                "dosage": "   ",
                "frequency": "Three times daily",
                "duration": "5 days",
            }
        ],
    }
    res_bad2 = client.post(
        "/api/v1/consultations/action",
        headers={"Authorization": f"Bearer {doc_token}"},
        json=bad_payload_fields,
    )
    assert res_bad2.status_code == 400
    assert "missing dosage" in res_bad2.json()["detail"]

    # 3. Successful prescription approval
    valid_payload = {
        "consultation_id": "c_phase5_rx_test",
        "action_type": "PRESCRIBE_MEDICATION",
        "clinical_notes": "Bacterial pharyngitis confirmed. Course of oral antibiotics and antipyretic.",
        "prescriptions": [
            {
                "medication_name": "Amoxicillin",
                "dosage": "500mg",
                "frequency": "TDS (8-hourly)",
                "duration": "5 days",
                "instructions": "Take after meals",
            },
            {
                "medication_name": "Paracetamol",
                "dosage": "650mg",
                "frequency": "SOS (as needed for fever)",
                "duration": "3 days",
                "instructions": "Maintain at least 6 hours gap between doses",
            },
        ],
    }
    res_valid = client.post(
        "/api/v1/consultations/action",
        headers={"Authorization": f"Bearer {doc_token}"},
        json=valid_payload,
    )
    assert res_valid.status_code == 200
    action_data = res_valid.json()
    assert action_data["action_type"] == "PRESCRIBE_MEDICATION"
    assert len(action_data["prescriptions"]) == 2
    assert action_data["doctor_registration_number"] == "TNMC-48291"

    # Verify consultation was updated in storage
    consultation = get_consultation("c_phase5_rx_test")
    assert consultation["status"] == "prescribed"
    assert len(consultation["prescriptions"]) == 2
    assert consultation["prescribed_by"] == "dr_rajan"
    assert consultation["doctor_registration_number"] == "TNMC-48291"
    assert "prescription_timestamp" in consultation


@patch("app.main.transcribe_and_translate")
@patch("app.main.translate_text")
@patch("app.main.text_to_speech")
def test_voice_prescription_back_translation_and_thread(mock_tts, mock_translate, mock_stt):
    """Verify voice prescription back-translation verification and voice thread persistence."""
    doc_token = create_token(
        user_id="dr_rajan",
        role=UserRole.DOCTOR,
        doctor_reg_no="TNMC-48291",
        is_verified_doctor=True,
    )

    mock_stt_result = AsyncMock()
    mock_stt_result.transcript_english = "Please take your fever medication twice daily and drink plenty of fluids."
    mock_stt.return_value = mock_stt_result

    # First call: English -> Tamil; Second call: Tamil -> English (back-translation)
    mock_translate.side_effect = [
        "தயவுசெய்து உங்கள் காய்ச்சல் மருந்தை தினமும் இரண்டு முறை எடுத்துக்கொள்ளுங்கள்.",
        "Please take your fever medicine twice daily and drink plenty of fluids.",
    ]
    mock_tts.return_value = "UklGRiQAAABXQVZFRm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA="

    res = client.post(
        "/api/v1/doctor/voice-prescription",
        headers={"Authorization": f"Bearer {doc_token}"},
        files={"audio": ("advice.wav", SAMPLE_WAV_HEADER, "audio/wav")},
        data={"patient_language": "ta-IN", "consultation_id": "c_phase5_rx_test"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["english_text"] == "Please take your fever medication twice daily and drink plenty of fluids."
    assert "காய்ச்சல்" in data["translated_text"]
    assert data["translation_verified"] is True
    assert data["back_translated_text"] is not None

    # Check voice thread persistence
    thread = get_voice_thread_manager().get_thread("c_phase5_rx_test")
    assert thread is not None
    assert len(thread.messages) >= 1
    latest_msg = thread.messages[-1]
    assert latest_msg.sender == "doctor"
    assert latest_msg.back_translated_text is not None
