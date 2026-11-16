"""
Tests for Phase 3: Consultation Pipeline, Triage Reliability, and Provenance Tracking.

Covers:
  1. Graceful degradation: Sarvam STT failure floors to DOCTOR_TODAY, blocks home remedies, and tags for clinician review.
  2. Graceful degradation: Gemini LLM failure falls back to deterministic extraction, floors to DOCTOR_TODAY.
  3. Clinical provenance tracking: InfoSource tagging on symptoms, red flags, and historical context.
  4. Background job idempotency: Deduplication of duplicate async consultation jobs using idempotency keys.
  5. Emergency safety invariant preservation: Degraded pipeline states NEVER downgrade red-flag emergency floors.
  6. Async HTTP endpoint idempotency: Repeated submission with X-Idempotency-Key returns existing job.
"""
import io
import time
import pytest
import numpy as np
import scipy.io.wavfile as wavfile
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

from app.main import app
from app.auth import create_token, UserRole
from app.pipeline import run_pipeline
from app.jobs.job_manager import JobManager, JobStatus, get_job_manager
from app.schemas import (
    InfoSource,
    PriorityLevel,
    UrgencyTier,
    TranscriptionResult,
    StructuredMedicalSummary,
    Symptom,
    RedFlag,
    AcousticBiomarkerResult,
)
from app.services.sarvam_stt import SarvamSTTError
from app.services.gemini_extract import extract_deterministic_summary

client = TestClient(app)


def _make_valid_wav(duration_s: float = 2.0, sample_rate: int = 16000) -> bytes:
    """Generate minimal valid PCM 16-bit mono WAV audio for pipeline testing."""
    t = np.linspace(0, duration_s, int(sample_rate * duration_s), endpoint=False)
    signal = 0.5 * np.sin(2 * np.pi * 440 * t)
    signal_int16 = (signal * 32767).astype(np.int16)
    out = io.BytesIO()
    wavfile.write(out, sample_rate, signal_int16)
    return out.getvalue()


@pytest.mark.asyncio
async def test_sarvam_stt_failure_floors_to_doctor_today():
    """
    If Sarvam STT times out or returns 5xx, the pipeline must:
      - Never crash or emit hallucinated clinical facts.
      - Tag transcription as unavailable and require human review.
      - Floor urgency tier to at least DOCTOR_TODAY.
      - Set model_used = 'fail_to_review'.
      - Strictly disallow home remedy guidance.
    """
    audio_bytes = _make_valid_wav()

    with patch("app.pipeline.sarvam_stt.transcribe_and_translate", side_effect=SarvamSTTError("Sarvam STT Gateway Timeout")):
        result = await run_pipeline(
            audio_bytes=audio_bytes,
            filename="patient_intake.wav",
            language_code="ta-IN",
            patient_context={"age": 28, "gender": "female"},
        )

    assert result.priority.urgency_tier == UrgencyTier.DOCTOR_TODAY
    assert result.priority.level in (PriorityLevel.MEDIUM, PriorityLevel.HIGH)
    assert result.priority.model_used == "fail_to_review"
    assert result.clinician_review_required is True
    assert result.home_remedy_guidance is None
    assert result.pipeline_stages["stt"]["status"] == "error"
    assert "transcription unavailable" in result.patient_input.transcript_english.lower()


@pytest.mark.asyncio
async def test_gemini_extraction_failure_falls_back_to_deterministic():
    """
    If Gemini LLM extraction times out or encounters quota limits, the pipeline must:
      - Safely catch the failure.
      - Produce an extraction failure note without halting.
      - Floor urgency tier to at least DOCTOR_TODAY.
      - Set model_used = 'fail_to_review' and clinician_review_required = True.
      - Strictly disallow home remedies.
    """
    audio_bytes = _make_valid_wav()
    mock_transcription = TranscriptionResult(
        transcript_original="எனக்கு கடுமையான காய்ச்சல் உள்ளது",
        transcript_english="I have severe fever and continuous headache",
        language_code="ta-IN",
        language_probability=0.98,
    )

    with patch("app.pipeline.sarvam_stt.transcribe_and_translate", new=AsyncMock(return_value=mock_transcription)), \
         patch("app.pipeline.gemini_extract.extract_structured_summary", side_effect=RuntimeError("Gemini API quota exhausted")):
        result = await run_pipeline(
            audio_bytes=audio_bytes,
            filename="patient_intake.wav",
            language_code="ta-IN",
            patient_context={"age": 30, "gender": "male"},
        )

    assert result.priority.urgency_tier == UrgencyTier.DOCTOR_TODAY
    assert result.priority.model_used == "fail_to_review"
    assert result.clinician_review_required is True
    assert result.home_remedy_guidance is None
    assert result.pipeline_stages["extraction"]["status"] == "error"
    assert "Extraction failure" in result.clinical_summary.extraction_notes


def test_clinical_provenance_tracking():
    """
    Verify explicit clinical provenance (InfoSource) on all clinical elements:
      - Symptoms extracted deterministically carry InfoSource.PATIENT_REPORTED.
      - Red flags carry InfoSource.PATIENT_REPORTED.
      - Prior record conditions can be tagged as InfoSource.PRIOR_RECORD_DERIVED.
      - Inferred metrics can be tagged as InfoSource.AI_INFERRED.
    """
    summary = extract_deterministic_summary(
        transcript_english="I have high fever and severe headache since yesterday",
        patient_context={"known_conditions": "asthma"},
    )

    assert len(summary.symptoms) > 0
    for s in summary.symptoms:
        assert s.source == InfoSource.PATIENT_REPORTED
        assert s.name in ("fever", "headache")

    # Verify custom provenance support for prior record derivation
    prior_symptom = Symptom(
        name="asthma",
        source=InfoSource.PRIOR_RECORD_DERIVED,
        confidence=0.95,
        raw_text="chronic medical history: asthma",
    )
    assert prior_symptom.source == InfoSource.PRIOR_RECORD_DERIVED
    assert prior_symptom.source.value == "prior_record_derived"

    # Verify custom provenance support for AI inference
    ai_inferred_symptom = Symptom(
        name="cough",
        source=InfoSource.AI_INFERRED,
        confidence=0.82,
        raw_text="detected via acoustic biomarker analysis",
    )
    assert ai_inferred_symptom.source == InfoSource.AI_INFERRED
    assert ai_inferred_symptom.source.value == "ai_inferred"


def test_job_manager_idempotency():
    """
    Verify JobManager deduplicates consultation jobs with identical idempotency keys:
      - Creating a job with key X returns job_id_1.
      - Calling create_job again with key X returns job_id_1 (no duplicate job record).
      - If the job failed, calling create_job with key X returns a fresh job_id to allow retries.
      - Calling cleanup_old_jobs purges expired idempotency keys cleanly.
    """
    mgr = JobManager()
    key = "idemp_consult_test_1001"

    # First submission
    job1_id = mgr.create_job(idempotency_key=key)
    assert job1_id.startswith("job_")

    # Second submission with same idempotency key must return the exact same job ID
    job2_id = mgr.create_job(idempotency_key=key)
    assert job2_id == job1_id

    # Lookup by idempotency key
    rec = mgr.get_job_by_idempotency_key(key)
    assert rec is not None
    assert rec.job_id == job1_id

    # If the job failed, allow retrying under the same key
    rec.status = JobStatus.FAILED
    job3_id = mgr.create_job(idempotency_key=key)
    assert job3_id != job1_id
    assert job3_id.startswith("job_")

    # Cleanup expired jobs
    mgr.cleanup_old_jobs(ttl_seconds=-1)
    assert mgr.get_job_by_idempotency_key(key) is None


@pytest.mark.asyncio
async def test_degraded_pipeline_preserves_emergency_floor():
    """
    Critical Invariant: Even if STT fails completely, non-downgradable emergency
    triggers (such as cardiac distress in elderly patients or severe acoustic distress)
    must STILL floor the urgency tier to EMERGENCY.
    """
    audio_bytes = _make_valid_wav()

    # Simulate elderly patient with chest pain context, but STT fails
    with patch("app.pipeline.sarvam_stt.transcribe_and_translate", side_effect=SarvamSTTError("STT Network Timeout")), \
         patch("app.pipeline.screen_safety") as mock_safety:
        from app.schemas import SafetyScreening, SafetyRedFlag
        mock_safety.return_value = SafetyScreening(
            has_critical_flags=True,
            override_priority=True,
            red_flags=[
                SafetyRedFlag(
                    category="cardiac",
                    symptom="chest_pain",
                    reason="Critical chest pain / cardiac distress in patient 65+",
                    severity="critical",
                    action="Immediate emergency response",
                )
            ],
        )

        result = await run_pipeline(
            audio_bytes=audio_bytes,
            filename="patient_elderly.wav",
            language_code="ta-IN",
            patient_context={"age": 72, "gender": "male", "chest_pain": True},
        )

    # Invariant: Emergency floor must not be lowered to doctor_today or doctor_soon
    assert result.priority.urgency_tier == UrgencyTier.EMERGENCY
    assert result.priority.emergency_override is True
    assert result.clinician_review_required is True
    assert result.home_remedy_guidance is None


def test_async_endpoint_idempotency_header():
    """
    Submitting the same audio with identical X-Idempotency-Key to
    /api/v1/pipeline/submit-audio returns the identical job_id.
    """
    token = create_token("user_idemp", UserRole.PATIENT, phone_number="+919876543210", account_id="acc_idemp_01")
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Idempotency-Key": "test_idemp_key_http_888",
    }
    audio_bytes = _make_valid_wav()

    res1 = client.post(
        "/api/v1/pipeline/submit-audio",
        files={"audio": ("consult.wav", audio_bytes, "audio/wav")},
        data={"language_code": "ta-IN", "age": "45"},
        headers=headers,
    )
    assert res1.status_code == 202
    job1_id = res1.json()["job_id"]

    # Re-submit with same X-Idempotency-Key
    res2 = client.post(
        "/api/v1/pipeline/submit-audio",
        files={"audio": ("consult.wav", audio_bytes, "audio/wav")},
        data={"language_code": "ta-IN", "age": "45"},
        headers=headers,
    )
    assert res2.status_code == 202
    job2_id = res2.json()["job_id"]

    assert job1_id == job2_id
