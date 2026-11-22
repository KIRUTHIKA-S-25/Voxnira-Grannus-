"""
Unit and Integration Tests for Phase 6: Patient Experience, Emergency SOS, Location Sharing, and Notifications.

Covers:
  1. Guest emergency walk-in with GPS location sharing (DPDP Act 2023 compliant)
  2. Geographic coordinate validation (-90 <= lat <= 90, -180 <= lon <= 180)
  3. Audio intake with optional location coordinates forwarding
  4. Patient retrieval of attending doctor's approved prescription and RMP registration
  5. Patient retrieval of bilingual doctor voice thread and back-translation verification
"""
import io
import time
import uuid
from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.auth import UserRole, create_token
from app.db import (
    save_consultation,
    get_consultation,
    get_guest_case,
    _MEM_ASSIGNMENTS,
)
from app.voice_threads import get_voice_thread_manager

client = TestClient(app)

SAMPLE_WAV_HEADER = b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00" + b"\x00" * 120


@pytest.fixture(autouse=True)
def clean_phase6_fixtures():
    _MEM_ASSIGNMENTS.pop("c_phase6_rx", None)
    _MEM_ASSIGNMENTS.pop("c_phase6_voice", None)

    save_consultation("c_phase6_rx", {
        "id": "c_phase6_rx",
        "urgency_tier": "doctor_today",
        "status": "triage",
        "chief_complaint": "Acute lower respiratory infection",
        "patient_language": "ta-IN",
        "account_id": "acc_phase6_patient",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    save_consultation("c_phase6_voice", {
        "id": "c_phase6_voice",
        "urgency_tier": "doctor_soon",
        "status": "triage",
        "chief_complaint": "Persistent seasonal allergic rhinitis",
        "patient_language": "hi-IN",
        "account_id": "acc_phase6_patient",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })


def test_guest_emergency_with_location():
    """Verify that walk-in emergency intake accepts and persists valid GPS coordinates."""
    payload = {
        "guest_name": "Arun Kumar",
        "age": "48",
        "gender": "male",
        "language_code": "ta-IN",
        "latitude": 13.0827,
        "longitude": 80.2707,
    }

    res = client.post("/api/v1/auth/guest-emergency", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert str(uuid.UUID(data["case_id"])) == data["case_id"]
    assert "108" in data["emergency_contact"]
    assert "does not dispatch an ambulance" in data["message"]

    case_id = data["case_id"]
    guest_case = get_guest_case(case_id)
    assert guest_case is not None
    assert guest_case["latitude"] == 13.0827
    assert guest_case["longitude"] == 80.2707

    consultation = get_consultation(case_id)
    assert consultation is not None
    assert consultation["latitude"] == 13.0827
    assert consultation["longitude"] == 80.2707
    assert consultation["urgency_tier"] == "emergency"


def test_guest_emergency_invalid_location_rejected():
    """Verify out-of-bounds geographic coordinates are strictly rejected with 400 Bad Request."""
    # Invalid latitude > 90
    bad_lat = {
        "guest_name": "Emergency Patient",
        "latitude": 95.5,
        "longitude": 77.0,
    }
    res_lat = client.post("/api/v1/auth/guest-emergency", json=bad_lat)
    assert res_lat.status_code == 400
    assert "Latitude must be between -90 and 90" in res_lat.json()["detail"]

    # Invalid longitude < -180
    bad_lon = {
        "guest_name": "Emergency Patient",
        "latitude": 12.0,
        "longitude": -185.0,
    }
    res_lon = client.post("/api/v1/auth/guest-emergency", json=bad_lon)
    assert res_lon.status_code == 400
    assert "Longitude must be between -180 and 180" in res_lon.json()["detail"]


def test_audio_intake_with_location():
    """Verify /api/v1/pipeline/submit-audio accepts optional GPS location and rejects invalid bounds."""
    pat_token = create_token(
        user_id="pat_chennai_01",
        role=UserRole.PATIENT,
        account_id="acc_chennai_01",
    )
    auth_headers = {"Authorization": f"Bearer {pat_token}"}
    consent_res = client.post(
        "/api/v1/consent/record", headers=auth_headers,
        json={"language_code": "ta-IN", "explicit_consent": True},
    )
    assert consent_res.status_code == 200

    # Invalid latitude rejected
    res_bad = client.post(
        "/api/v1/pipeline/submit-audio",
        headers=auth_headers,
        files={"audio": ("intake.wav", SAMPLE_WAV_HEADER, "audio/wav")},
        data={"latitude": 105.0, "longitude": 80.0, "consent_id": consent_res.json()["consent_id"]},
    )
    assert res_bad.status_code == 400
    assert "Latitude must be between -90 and 90" in res_bad.json()["detail"]

    # Valid coordinates accepted
    res_ok = client.post(
        "/api/v1/pipeline/submit-audio",
        headers=auth_headers,
        files={"audio": ("intake.wav", SAMPLE_WAV_HEADER, "audio/wav")},
        data={
            "latitude": 13.0827,
            "longitude": 80.2707,
            "language_code": "ta-IN",
            "consent_id": consent_res.json()["consent_id"],
        },
    )
    assert res_ok.status_code == 202
    assert "job_id" in res_ok.json()


def test_patient_retrieves_approved_prescription():
    """Verify patient can retrieve attending doctor's approved prescription and RMP registration number."""
    from app.db import save_consultation, save_assignment
    save_consultation("c_phase6_rx", {
        "id": "c_phase6_rx",
        "account_id": "acc_phase6_patient",
        "urgency_tier": "doctor_today",
        "status": "triage",
    })
    save_assignment(
        consultation_id="c_phase6_rx",
        doctor_id="dr_priya",
        assigned_by="system",
        status="assigned",
        reassignment_reason="Prescription evaluation",
    )
    doc_token = create_token(
        user_id="dr_priya",
        role=UserRole.DOCTOR,
        doctor_reg_no="TNMC-54321",
        state_council="Tamil Nadu Medical Council",
        is_verified_doctor=True,
    )
    pat_token = create_token(
        user_id="pat_01",
        role=UserRole.PATIENT,
        account_id="acc_phase6_patient",
    )

    # 1. Doctor records prescription
    action_payload = {
        "consultation_id": "c_phase6_rx",
        "action_type": "PRESCRIBE_MEDICATION",
        "clinical_notes": "Mild wheezing. Bronchodilator inhaler and antiallergic prescribed.",
        "prescriptions": [
            {
                "medication_name": "Levocetirizine",
                "dosage": "5mg",
                "frequency": "OD at bedtime",
                "duration": "7 days",
                "instructions": "May cause mild drowsiness",
            },
            {
                "medication_name": "Salbutamol Inhaler",
                "dosage": "100mcg",
                "frequency": "2 puffs SOS",
                "duration": "14 days",
                "instructions": "Rinse mouth after inhalation",
            },
        ],
    }
    res_doc = client.post(
        "/api/v1/consultations/action",
        headers={"Authorization": f"Bearer {doc_token}"},
        json=action_payload,
    )
    assert res_doc.status_code == 200

    # 2. Patient queries consultation details
    res_pat = client.get(
        "/api/v1/consultations/c_phase6_rx",
        headers={"Authorization": f"Bearer {pat_token}"},
    )
    assert res_pat.status_code == 200
    details = res_pat.json()
    assert details["status"] == "prescribed"
    assert len(details["prescriptions"]) == 2
    assert details["doctor_registration_number"] == "TNMC-54321"
    assert details["prescribed_by"] == "dr_priya"
    assert details["prescriptions"][0]["medication_name"] == "Levocetirizine"
    assert details["prescriptions"][0]["dosage"] == "5mg"


def test_patient_retrieves_doctor_voice_note():
    """Verify patient can retrieve doctor's translated audio note and verified back-translation."""
    pat_token = create_token(
        user_id="pat_01",
        role=UserRole.PATIENT,
        account_id="acc_phase6_patient",
    )

    # Add message to consultation voice thread
    thread_mgr = get_voice_thread_manager()
    thread_mgr.add_message(
        consultation_id="c_phase6_voice",
        sender="doctor",
        original_text="Take the tablet before bed and call back if fever persists.",
        translated_text="सोने से पहले गोली लें और अगर बुखार बना रहे तो वापस कॉल करें।",
        original_language="en-IN",
        target_language="hi-IN",
        back_translated_text="Take the pill before going to sleep and call back if fever persists.",
        audio_base64="UklGRiQAAABXQVZFRm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA=",
    )

    # Patient retrieves voice thread
    res = client.get(
        "/api/v1/consultations/c_phase6_voice/voice-thread",
        headers={"Authorization": f"Bearer {pat_token}"},
    )
    assert res.status_code == 200
    thread = res.json()
    assert thread["consultation_id"] == "c_phase6_voice"
    assert len(thread["messages"]) >= 1

    msg = thread["messages"][-1]
    assert msg["sender"] == "doctor"
    assert msg["target_language"] == "hi-IN"
    assert "सोने" in msg["translated_text"]
    assert msg["back_translated_text"] is not None
    assert msg["audio_base64"] is not None
