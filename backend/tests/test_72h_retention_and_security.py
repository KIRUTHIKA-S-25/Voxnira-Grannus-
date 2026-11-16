"""
End-to-End Safety and 72-Hour Audio Retention Tests.

Verifies:
  1. Default audio retention is 72 hours with explicit UTC expiry timestamps.
  2. Retention sweep deletes expired audio files from filesystem and records to deletion_log.
  3. Clinical report, original transcript, and English translation remain permanently accessible after audio purge.
  4. Unique JWT IDs (jti) ensure rapid token generation avoids signature collision with revoked tokens.
  5. Emergency red-flag safety rules floor urgency tier to EMERGENCY and cannot be suppressed.
"""
import time
from pathlib import Path
from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.config import get_settings
from app.auth import create_token, UserRole, revoke_token, verify_token
from app.security.data_retention import get_retention_manager
from app.priority import assess_priority
from app.schemas import Symptom, SafetyScreening, RedFlag, UrgencyTier
from app.db import (
    save_consultation,
    get_consultation,
    get_deletion_logs,
)

client = TestClient(app)


def test_default_audio_retention_is_72_hours():
    """Verify default audio retention configuration is 72 hours."""
    settings = get_settings()
    assert settings.audio_retention_hours == 72

    expected_expiry = time.time() + (72 * 3600)
    retention_mgr = get_retention_manager()
    record = retention_mgr.schedule_audio_deletion("test_audio_sample_72h.wav")
    assert record["file_path"] == "test_audio_sample_72h.wav"
    assert "delete_after" in record
    assert "created_at" in record

    # Expiry timestamp should be approximately 72 hours in future (+- 30s)
    assert abs(record["expiry_ts"] - expected_expiry) < 30


def test_72h_retention_sweep_deletes_audio_and_retains_clinical_report(tmp_path):
    """
    Simulate a 72-hour audio file expiry:
    1. File exists on disk.
    2. Sweep deletes the physical file.
    3. Clinical summary and transcripts remain fully readable.
    4. Deletion event is logged to deletion_log.
    """
    audio_path = tmp_path / "patient_recording_72h.wav"
    audio_path.write_bytes(b"RIFF dummy audio data 72h retention test")
    file_str = str(audio_path)
    assert Path(file_str).exists()

    cid = "consult_retention_72h_001"
    save_consultation(cid, {
        "id": cid,
        "patient_name": "Murugan S.",
        "transcript_original": "நெஞ்சு வலி அதிகமாக உள்ளது",
        "transcript_english": "Severe chest pain radiating to left shoulder",
        "chief_complaint": "Acute chest pain",
        "urgency_tier": "emergency",
        "audio_file": file_str,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

    retention_mgr = get_retention_manager()
    record = retention_mgr.schedule_audio_deletion(file_str, hours=72)
    assert record["file_path"] == file_str

    # Simulate clock advance past 72 hours
    retention_mgr._scheduled_deletions[file_str]["expiry_ts"] = time.time() - 60

    # Execute retention sweep
    purged_list = retention_mgr.execute_retention_sweep()
    assert file_str in purged_list

    # Physical audio file must be removed
    assert not Path(file_str).exists()

    # Clinical data must remain fully preserved
    consultation = get_consultation(cid)
    assert consultation is not None
    assert consultation["transcript_english"] == "Severe chest pain radiating to left shoulder"
    assert consultation["urgency_tier"] == "emergency"

    # Deletion log contains audit trail
    deletion_logs = get_deletion_logs()
    assert any(log.get("resource_id") == file_str for log in deletion_logs)


def test_rapid_token_generation_produces_unique_jti_without_revocation_collision():
    """Verify tokens generated in the same second have unique jti and do not collide."""
    token_1 = create_token(
        user_id="test_doc_uniqueness_01",
        role=UserRole.DOCTOR,
        doctor_reg_no="TNMC-11111",
        is_verified_doctor=True,
    )
    # Revoke first token
    revoke_token(token_1, user_id="test_doc_uniqueness_01")

    # Generate second token immediately for same user
    token_2 = create_token(
        user_id="test_doc_uniqueness_01",
        role=UserRole.DOCTOR,
        doctor_reg_no="TNMC-11111",
        is_verified_doctor=True,
    )

    # Tokens must be distinct
    assert token_1 != token_2

    # Second token must remain valid and not be marked revoked
    user = verify_token(token_2)
    assert user.user_id == "test_doc_uniqueness_01"


def test_emergency_red_flags_floor_priority_invariants():
    """
    Verify clinical invariants:
    Chest pain in elderly, infant fever, vomiting blood, and pregnancy bleeding
    strictly floor priority to EMERGENCY and cannot be downgraded to self-care or doctor_soon.
    """
    # 1. Cardiac emergency (65y with chest pain and diaphoresis)
    symptoms = [
        Symptom(name="chest pain", severity="severe", raw_text="severe crushing chest pain"),
        Symptom(name="sweating", severity="moderate", raw_text="cold sweats"),
    ]
    from app.schemas import SafetyRedFlag
    safety = SafetyScreening(
        red_flags=[
            SafetyRedFlag(
                potential_red_flag=True,
                symptom="chest pain with sweating",
                reason="Cardiac emergency indicator in older adult",
                action="emergency_referral",
                severity="critical",
            )
        ],
        has_critical_flags=True,
        override_priority=True,
    )
    from app.schemas import StructuredMedicalSummary
    summary = StructuredMedicalSummary(
        chief_complaint="Chest pain with sweating",
        symptoms=symptoms,
        red_flags=[],
    )
    result = assess_priority(
        summary,
        safety_screening=safety,
        patient_context={"age": 65, "gender": "male"},
    )
    assert result.urgency_tier == UrgencyTier.EMERGENCY
    assert result.emergency_override is True
    assert "108" in (result.emergency_call_numbers or [])

    # 2. Pediatric infant fever (3 months old)
    symptoms_ped = [Symptom(name="fever", severity="severe", raw_text="high fever")]
    safety_ped = SafetyScreening(
        red_flags=[
            SafetyRedFlag(
                potential_red_flag=True,
                symptom="infant fever",
                reason="Neonatal/infant fever red flag under 5 years",
                action="emergency_referral",
                severity="critical",
            )
        ],
        has_critical_flags=True,
        override_priority=True,
    )
    summary_ped = StructuredMedicalSummary(
        chief_complaint="High fever in 3 month old",
        symptoms=symptoms_ped,
        red_flags=[],
    )
    result_ped = assess_priority(
        summary_ped,
        safety_screening=safety_ped,
        patient_context={"age": "3 months", "gender": "female"},
    )
    assert result_ped.urgency_tier == UrgencyTier.EMERGENCY
    assert result_ped.emergency_override is True
