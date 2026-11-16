"""
End-to-End Verification Test for Complete Patient & Doctor Healthcare Journey:
1. Patient Phone Login with Mandatory OTP Verification.
2. Structured Family Profiles under Same Phone Account.
3. Emergency Voice Intake: Spoken Name Extraction ("My name is...") & Emergency DB Storage.
4. Voice Request Pipeline: Specialist Routing (Cardiology/Pediatrics) & Fallback to General Physician.
5. General Doctor Workflow: Case Claiming, Reassignment, and Medication Prescription with NMC Registration.
6. Complete Database Storage: Patient Name, Phone, Demographics, Clinical Report, and Prescriptions.
"""
import io
import wave
import pytest
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient

from app.main import app
from app.db import (
    _MEM_ACCOUNTS,
    _MEM_PROFILES,
    _MEM_OTPS,
    _MEM_CONSULTATIONS,
    _MEM_ASSIGNMENTS,
    _OTP_COOLDOWNS,
    DEV_TEST_NUMBERS,
    get_consultation,
    get_patient_profile,
    get_patient_account_by_id,
    save_consultation,
)
from app.telehealth.clinical_actions import get_clinical_action_manager
from app.schemas import (
    StructuredMedicalSummary,
    TranscriptionResult,
    PriorityAssessment,
    PriorityLevel,
    UrgencyTier,
    Symptom,
    RedFlag,
    InfoSource,
)
from app.pipeline import extract_stated_patient_name
from app.routing import categorize_complaint, select_best_doctor


@pytest.fixture(autouse=True)
def clean_stores():
    _MEM_ACCOUNTS.clear()
    _MEM_PROFILES.clear()
    _MEM_OTPS.clear()
    _MEM_CONSULTATIONS.clear()
    _MEM_ASSIGNMENTS.clear()
    _OTP_COOLDOWNS.clear()
    get_clinical_action_manager()._history.clear()
    yield
    _MEM_ACCOUNTS.clear()
    _MEM_PROFILES.clear()
    _MEM_OTPS.clear()
    _MEM_CONSULTATIONS.clear()
    _MEM_ASSIGNMENTS.clear()
    _OTP_COOLDOWNS.clear()
    get_clinical_action_manager()._history.clear()


def make_dummy_wav(duration_seconds=2.0, sample_rate=16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        num_frames = int(duration_seconds * sample_rate)
        wf.writeframes(b"\x00\x00" * num_frames)
    return buf.getvalue()


client = TestClient(app)


# -----------------------------------------------------------------------------
# 1. Phone Login & Mandatory OTP Verification
# -----------------------------------------------------------------------------
def test_phone_login_requires_mandatory_otp():
    """Verify phone login cannot be bypassed and requires valid single-use OTP."""
    phone = "+919876543210"

    # Step 1: Request OTP
    req_res = client.post("/api/v1/auth/otp/request", json={"phone_number": phone})
    assert req_res.status_code == 200
    assert req_res.json()["success"] is True

    # Step 2: Cannot verify with invalid code -> returns 401 Unauthorized
    bad_res = client.post("/api/v1/auth/otp/verify", json={"phone_number": phone, "otp_code": "000000"})
    assert bad_res.status_code == 401
    assert "Invalid verification code" in bad_res.json()["detail"]

    # Step 3: Verify with correct dev code
    test_otp = DEV_TEST_NUMBERS[phone]
    good_res = client.post("/api/v1/auth/otp/verify", json={"phone_number": phone, "otp_code": test_otp})
    assert good_res.status_code == 200
    data = good_res.json()
    assert "token" in data
    assert data["role"] == "patient"
    assert "account_id" in data

    token = data["token"]
    assert len(token) > 20

    # Step 4: Verify single-use property (replaying the same OTP fails)
    replay_res = client.post("/api/v1/auth/otp/verify", json={"phone_number": phone, "otp_code": test_otp})
    assert replay_res.status_code == 401


# -----------------------------------------------------------------------------
# 2. Multi-Profile Family Management under Same Phone
# -----------------------------------------------------------------------------
def test_family_multi_profiles_under_same_mobile():
    """Verify multiple family members can create and maintain profiles on a single mobile account."""
    phone = "+919876543210"
    client.post("/api/v1/auth/otp/request", json={"phone_number": phone})
    test_otp = DEV_TEST_NUMBERS[phone]
    auth_data = client.post("/api/v1/auth/otp/verify", json={"phone_number": phone, "otp_code": test_otp}).json()
    token = auth_data["token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Create Father Profile (Primary)
    father_res = client.post("/api/v1/patient/profiles", json={
        "full_name": "Ramesh Sharma",
        "age": 45,
        "gender": "male",
        "relation": "self",
        "preferred_language": "hi-IN",
        "known_conditions": ["hypertension"],
        "allergies": [],
        "medications": ["amlodipine"],
    }, headers=headers)
    assert father_res.status_code == 200
    father_profile = father_res.json()
    assert father_profile["full_name"] == "Ramesh Sharma"

    # Create Daughter Profile (Child / Dependent)
    child_res = client.post("/api/v1/patient/profiles", json={
        "full_name": "Ananya Sharma",
        "age": 7,
        "gender": "female",
        "relation": "daughter",
        "preferred_language": "hi-IN",
        "guardian_name": "Ramesh Sharma",
        "is_caregiver_managed": True,
        "known_conditions": [],
        "allergies": ["penicillin"],
        "medications": [],
    }, headers=headers)
    assert child_res.status_code == 200
    child_profile = child_res.json()
    assert child_profile["full_name"] == "Ananya Sharma"
    assert child_profile["is_caregiver_managed"] is True

    # List all profiles under this phone account
    list_res = client.get("/api/v1/patient/profiles", headers=headers)
    assert list_res.status_code == 200
    profiles = list_res.json()
    assert len(profiles) == 2
    names = {p["full_name"] for p in profiles}
    assert "Ramesh Sharma" in names
    assert "Ananya Sharma" in names


# -----------------------------------------------------------------------------
# 3. Emergency Spoken Name Extraction from Voice & Emergency DB Storage
# -----------------------------------------------------------------------------
def test_emergency_voice_name_extraction_and_db_storage():
    """Verify that if an emergency patient speaks their name in voice, it is extracted and stored in DB."""
    # Test deterministic spoken name extraction helper
    spoken_tx_en = "My name is Murugan. I have unbearable crushing chest pain and shortness of breath."
    extracted = extract_stated_patient_name(spoken_tx_en)
    assert extracted == "Murugan"

    # Test Tamil spoken name
    spoken_tx_ta = "என் பெயர் முருகன். நெஞ்சு வலி அதிகமாக உள்ளது."
    extracted_ta = extract_stated_patient_name("", spoken_tx_ta)
    assert extracted_ta == "முருகன்"

    # Test Hindi spoken name
    spoken_tx_hi = "मेरा नाम मोहन है मुझे सांस लेने में तकलीफ है"
    extracted_hi = extract_stated_patient_name("", spoken_tx_hi)
    assert extracted_hi == "मोहन"

    # Test emergency audio intake where patient speaks their name
    wav_bytes = make_dummy_wav()
    mock_transcription = TranscriptionResult(
        transcript_original="என் பெயர் முருகன். நெஞ்சு வலி அதிகமாக உள்ளது.",
        transcript_english="My name is Murugan. I have severe chest pain.",
        language_code="ta-IN",
        language_probability=0.98,
    )
    mock_summary = StructuredMedicalSummary(
        chief_complaint="severe chest pain",
        stated_patient_name="Murugan",
        symptoms=[
            Symptom(name="chest pain", severity="severe", raw_text="severe chest pain", source=InfoSource.PATIENT_REPORTED)
        ],
        red_flags=[
            RedFlag(phrase="severe chest pain", source=InfoSource.PATIENT_REPORTED)
        ],
    )

    # Guest emergency intake
    guest_res = client.post("/api/v1/auth/guest-emergency", json={
        "guest_name": "Emergency Patient",
        "age": 52,
        "gender": "male",
        "language_code": "ta-IN",
    })
    assert guest_res.status_code == 200
    guest_data = guest_res.json()
    guest_token = guest_data["guest_token"]
    case_id = guest_data["case_id"]

    # Submit voice under guest emergency token
    with patch("app.pipeline.sarvam_stt.transcribe_and_translate", new=AsyncMock(return_value=mock_transcription)), \
         patch("app.pipeline.gemini_extract.extract_structured_summary", new=AsyncMock(return_value=mock_summary)):

        resp = client.post(
            "/api/v1/pipeline/process-audio",
            files={"audio": ("emergency_intake.wav", wav_bytes, "audio/wav")},
            data={"language_code": "ta-IN"},
            headers={"Authorization": f"Bearer {guest_token}"},
        )
        assert resp.status_code == 200
        result = resp.json()

        # Emergency floor guaranteed
        assert result["priority"]["urgency_tier"] == "emergency"
        assert result["priority"]["level"] == "HIGH"

        # Check DB persistence
        cid = result["request_id"]
        saved = get_consultation(cid)
        assert saved is not None
        assert saved["urgency_tier"] == "emergency"
        assert saved["patient_name"] == "Murugan"
        assert saved["stated_patient_name"] == "Murugan"
        assert "severe chest pain" in saved["chief_complaint"]


# -----------------------------------------------------------------------------
# 4. Specialist Routing (Cardiology/Pediatrics) & Fallback to General Physician
# -----------------------------------------------------------------------------
def test_specialist_routing_and_general_physician_fallback():
    """Verify complaint categorization maps to specialist, and falls back to General Medicine if unavailable."""
    # Scenario A: Adult with cardiac chest symptoms -> CARDIOLOGY
    cardiac_summary = StructuredMedicalSummary(
        chief_complaint="crushing chest pain radiating to arm",
        symptoms=[Symptom(name="chest pain", severity="severe", raw_text="chest pain")],
    )
    cat, dept, rationale = categorize_complaint(cardiac_summary, patient_context={"age": 58})
    assert dept == "CARDIOLOGY"
    assert cat == "cardiac_symptoms"

    # Scenario B: Child with fever -> PEDIATRICS
    pediatric_summary = StructuredMedicalSummary(
        chief_complaint="high fever with shivering",
        symptoms=[Symptom(name="fever", severity="severe", raw_text="high fever")],
    )
    cat_ped, dept_ped, rat_ped = categorize_complaint(pediatric_summary, patient_context={"age": 7})
    assert dept_ped == "PEDIATRICS"
    assert cat_ped == "pediatric_illness"

    # Scenario C: Specialist Busy / Unavailable -> Fallback to General Medicine
    # Mock doctors where Pediatric specialist is overloaded (capacity 25, load 25)
    doctors = [
        {
            "id": "doc_ped_1",
            "full_name": "Dr. Kavitha (Pediatrician)",
            "specialty": "PEDIATRICS",
            "department_code": "PEDIATRICS",
            "is_on_duty": True,
            "is_active": True,
            "active_case_load": 25,  # at max capacity
            "max_capacity": 25,
            "languages": ["hi", "en"],
        },
        {
            "id": "doc_gen_1",
            "full_name": "Dr. Rajesh (General Medicine)",
            "specialty": "GENERAL_MEDICINE",
            "department_code": "GEN_MED",
            "is_on_duty": True,
            "is_active": True,
            "active_case_load": 5,  # available
            "max_capacity": 25,
            "languages": ["hi", "en"],
        },
    ]
    best_doc, selection_rationale = select_best_doctor(doctors, department_code="PEDIATRICS", patient_language="hi-IN")
    assert best_doc is not None
    assert best_doc["id"] == "doc_gen_1"
    assert "General Medicine" in selection_rationale


# -----------------------------------------------------------------------------
# 5. Doctor Treatment Process: Case Claim, Reassign, and E-Prescription
# -----------------------------------------------------------------------------
def test_doctor_claim_reassign_and_prescription_workflow():
    """Verify attending doctor can claim case, reassign to specialist OR issue valid clinical prescription."""
    from app.auth import create_token, UserRole

    # Create doctor credentials (RMP verified)
    doc_id = "11111111-0001-0000-0000-000000000001"
    doc_token = create_token(
        user_id=doc_id,
        role=UserRole.DOCTOR,
        is_verified_doctor=True,
        doctor_reg_no="NMC-2024-889900",
    )
    doc_headers = {"Authorization": f"Bearer {doc_token}"}

    # Create a consultation in triage
    cid = "c_journey_test_001"
    save_consultation(cid, {
        "id": cid,
        "patient_name": "Ananya Sharma",
        "phone_number": "+919876543210",
        "age": 7,
        "gender": "female",
        "chief_complaint": "fever and cold",
        "urgency_tier": "doctor_today",
        "status": "triage",
    })

    # Step A: Doctor claims case
    claim_res = client.post(f"/api/v1/consultations/{cid}/claim", headers=doc_headers)
    assert claim_res.status_code == 200

    # Step B: Doctor prescribes medicine
    rx_payload = {
        "consultation_id": cid,
        "action_type": "PRESCRIBE_MEDICATION",
        "clinical_notes": "Mild viral pyrexia. Maintain adequate oral hydration.",
        "prescriptions": [
            {
                "medication_name": "Paracetamol Paediatric Syrup 120mg/5ml",
                "dosage": "5 ml",
                "frequency": "Thrice daily (TDS)",
                "duration": "3 days",
                "instructions": "After feeds. Review if fever persists after 72 hours.",
            }
        ],
    }
    action_res = client.post("/api/v1/consultations/action", json=rx_payload, headers=doc_headers)
    assert action_res.status_code == 200
    act_data = action_res.json()
    assert act_data["doctor_registration_number"] == "NMC-2024-889900"

    # Step C: Verify DB contains complete patient name, phone, report, and prescription
    stored = get_consultation(cid)
    assert stored is not None
    assert stored["patient_name"] == "Ananya Sharma"
    assert stored["phone_number"] == "+919876543210"
    assert stored["age"] == 7
    assert stored["status"] == "prescribed"
    assert len(stored["prescriptions"]) == 1
    assert stored["prescriptions"][0]["medication_name"] == "Paracetamol Paediatric Syrup 120mg/5ml"
    assert stored["prescriptions"][0]["dosage"] == "5 ml"
    assert stored["prescriptions"][0]["duration"] == "3 days"
    assert stored["doctor_registration_number"] == "NMC-2024-889900"


# -----------------------------------------------------------------------------
# 6. Full End-to-End Async Voice Consultation Pipeline with Demographics
# -----------------------------------------------------------------------------
def test_e2e_async_voice_consultation_stores_all_patient_fields():
    """Verify async voice consultation preserves patient name, phone number, and report in DB."""
    phone = "+919876543210"
    client.post("/api/v1/auth/otp/request", json={"phone_number": phone})
    test_otp = DEV_TEST_NUMBERS[phone]
    auth_data = client.post("/api/v1/auth/otp/verify", json={"phone_number": phone, "otp_code": test_otp}).json()
    token = auth_data["token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Create profile
    prof = client.post("/api/v1/patient/profiles", json={
        "full_name": "Sundaramoorthy",
        "age": 62,
        "gender": "male",
        "relation": "self",
        "preferred_language": "ta-IN",
        "known_conditions": ["type 2 diabetes"],
    }, headers=headers).json()
    profile_id = prof["id"]

    wav_bytes = make_dummy_wav()
    mock_transcription = TranscriptionResult(
        transcript_original="எனக்கு இரண்டு நாட்களாக கடுமையான இருமல் மற்றும் மூச்சுத்திணறல் உள்ளது.",
        transcript_english="I have had severe cough and breathlessness for two days.",
        language_code="ta-IN",
        language_probability=0.95,
    )
    mock_summary = StructuredMedicalSummary(
        chief_complaint="severe cough and breathlessness",
        stated_patient_name="Sundaramoorthy",
        symptoms=[
            Symptom(name="cough", severity="severe", raw_text="severe cough"),
            Symptom(name="difficulty breathing", severity="severe", raw_text="breathlessness"),
        ],
        existing_conditions=["type 2 diabetes"],
    )

    with patch("app.pipeline.sarvam_stt.transcribe_and_translate", new=AsyncMock(return_value=mock_transcription)), \
         patch("app.pipeline.gemini_extract.extract_structured_summary", new=AsyncMock(return_value=mock_summary)):

        # Submit audio consultation with profile
        sub_res = client.post(
            "/api/v1/pipeline/process-audio",
            files={"audio": ("voice_intake.wav", wav_bytes, "audio/wav")},
            data={
                "language_code": "ta-IN",
                "profile_id": profile_id,
                "patient_name": "Sundaramoorthy",
            },
            headers=headers,
        )
        assert sub_res.status_code == 200
        result = sub_res.json()
        req_id = result["request_id"]

        # Verify database record integrity
        consultation = get_consultation(req_id)
        assert consultation is not None
        assert consultation["patient_name"] == "Sundaramoorthy"
        assert consultation["phone_number"] == phone
        assert consultation["profile_id"] == profile_id
        assert consultation["status"] == "triage"
        assert consultation["original_transcript"] == mock_transcription.transcript_original
        assert consultation["english_transcript"] == mock_transcription.transcript_english
        assert consultation["chief_complaint"] == "severe cough and breathlessness"
        assert consultation["assigned_doctor_id"] is not None  # Specialist or General doctor assigned
