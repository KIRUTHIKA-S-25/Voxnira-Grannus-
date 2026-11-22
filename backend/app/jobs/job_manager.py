"""
Asynchronous Job Engine and Stage Progress Tracker for Grannus RuralCare AI.

Enables:
  - Non-blocking asynchronous consultation processing (202 Accepted)
  - Granular stage-by-stage progress polling for rural, low-bandwidth connections
  - Decoupling heavy STT, acoustic analysis, and LLM extraction from synchronous HTTP threads
"""
import asyncio
import time
import uuid
import logging
from datetime import datetime
from enum import Enum
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field

from app.pipeline import run_pipeline
from app.schemas import PipelineResult
from app.observability.metrics import get_metrics
from app.security.audit_logger import get_audit_logger

logger = logging.getLogger("rural_care.jobs")


class JobStatus(str, Enum):
    QUEUED = "QUEUED"
    PREPROCESSING = "PREPROCESSING"
    TRANSCRIBING = "TRANSCRIBING"
    EXTRACTING = "EXTRACTING"
    TRIAGING = "TRIAGING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class JobProgressResponse(BaseModel):
    job_id: str
    status: JobStatus
    progress_pct: int
    current_stage: str
    created_at: float
    updated_at: float
    result: Optional[PipelineResult] = None
    error: Optional[str] = None


class JobRecord:
    def __init__(self, job_id: str, account_id: Optional[str] = None, guest_case_id: Optional[str] = None):
        self.job_id = job_id
        self.account_id = account_id
        self.guest_case_id = guest_case_id
        self.status = JobStatus.QUEUED
        self.progress_pct = 0
        self.current_stage = "Job queued for processing"
        self.created_at = time.time()
        self.updated_at = time.time()
        self.result: Optional[PipelineResult] = None
        self.error: Optional[str] = None

    def update_stage(self, status: JobStatus, progress_pct: int, stage_desc: str):
        self.status = status
        self.progress_pct = progress_pct
        self.current_stage = stage_desc
        self.updated_at = time.time()
        try:
            from app.db import save_pipeline_job
            save_pipeline_job(self.job_id, {
                "status": status.value,
                "stage": stage_desc,
                "progress": progress_pct,
                "account_id": self.account_id,
                "guest_case_id": self.guest_case_id,
                "created_at": self.created_at,
                "result": (self.result.model_dump() if hasattr(self.result, "model_dump") else self.result.dict()) if self.result else None,
                "error_message": self.error,
            })
        except Exception as exc:
            from app.config import get_settings
            if get_settings().is_production:
                logger.error("Could not persist pipeline job stage (%s)", type(exc).__name__)
                raise RuntimeError("Pipeline job state could not be persisted.") from exc
            logger.warning("Could not persist pipeline job stage (%s)", type(exc).__name__)

    def to_response(self) -> JobProgressResponse:
        return JobProgressResponse(
            job_id=self.job_id,
            status=self.status,
            progress_pct=self.progress_pct,
            current_stage=self.current_stage,
            created_at=self.created_at,
            updated_at=self.updated_at,
            result=self.result,
            error=self.error,
        )


class JobManager:
    def __init__(self):
        self._jobs: Dict[str, JobRecord] = {}
        self._idempotency_keys: Dict[str, str] = {}

    def create_job(
        self,
        idempotency_key: Optional[str] = None,
        account_id: Optional[str] = None,
        guest_case_id: Optional[str] = None,
        audio_bytes: Optional[bytes] = None,
        filename: Optional[str] = None,
        language_code: Optional[str] = None,
        patient_context: Optional[dict] = None,
        doctor_preferred_language: Optional[str] = None,
    ) -> str:
        """Create a new job record with durable recovery metadata or return active job matching idempotency_key."""
        if idempotency_key and idempotency_key in self._idempotency_keys:
            existing_job_id = self._idempotency_keys[idempotency_key]
            existing_job = self.get_job(existing_job_id)
            if existing_job and existing_job.status != JobStatus.FAILED:
                return existing_job_id

        job_id = f"job_{uuid.uuid4().hex[:12]}"
        rec = JobRecord(job_id, account_id=account_id, guest_case_id=guest_case_id)
        self._jobs[job_id] = rec
        if idempotency_key:
            self._idempotency_keys[idempotency_key] = job_id
        try:
            from app.db import save_pipeline_job
            import base64
            from app.security.phi_protection import encrypt_phi_field
            enc_audio = None
            if audio_bytes:
                b64 = base64.b64encode(audio_bytes).decode("ascii")
                enc_audio = encrypt_phi_field(b64)

            save_pipeline_job(job_id, {
                "status": rec.status.value,
                "stage": rec.current_stage,
                "progress": rec.progress_pct,
                "account_id": rec.account_id,
                "guest_case_id": rec.guest_case_id,
                "created_at": rec.created_at,
                "encrypted_audio_payload": enc_audio,
                "audio_filename": filename,
                "language_code": language_code,
                "patient_context": patient_context,
                "doctor_preferred_language": doctor_preferred_language,
            })
        except Exception as exc:
            from app.config import get_settings
            self._jobs.pop(job_id, None)
            if idempotency_key and self._idempotency_keys.get(idempotency_key) == job_id:
                self._idempotency_keys.pop(idempotency_key, None)
            if get_settings().is_production:
                logger.error("Could not persist new pipeline job (%s)", type(exc).__name__)
                raise RuntimeError("Pipeline job could not be persisted.") from exc
            logger.warning("Could not persist new pipeline job (%s)", type(exc).__name__)
        return job_id

    def get_job_by_idempotency_key(self, idempotency_key: str) -> Optional[JobRecord]:
        if not idempotency_key:
            return None
        job_id = self._idempotency_keys.get(idempotency_key)
        if job_id:
            return self.get_job(job_id)
        return None

    def get_job(self, job_id: str) -> Optional[JobRecord]:
        try:
            from app.db import get_pipeline_job
            data = get_pipeline_job(job_id)
            if data:
                rec = JobRecord(
                    job_id,
                    account_id=data.get("account_id"),
                    guest_case_id=data.get("guest_case_id"),
                )
                rec.status = JobStatus(data.get("status", "QUEUED"))
                rec.progress_pct = data.get("progress", 0)
                rec.current_stage = data.get("stage", "")
                result = data.get("result")
                if result:
                    rec.result = PipelineResult.model_validate(result)
                rec.error = data.get("error_message")
                created_at = data.get("created_at")
                if isinstance(created_at, str):
                    rec.created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00")).timestamp()
                elif isinstance(created_at, (int, float)):
                    rec.created_at = float(created_at)
                updated_at = data.get("updated_at")
                if isinstance(updated_at, str):
                    rec.updated_at = datetime.fromisoformat(updated_at.replace("Z", "+00:00")).timestamp()
                self._jobs[job_id] = rec
                return rec
        except Exception as exc:
            from app.config import get_settings
            if get_settings().is_production:
                logger.error("Could not load persisted pipeline job (%s)", type(exc).__name__)
                raise RuntimeError("Pipeline job state is temporarily unavailable.") from exc
            logger.warning("Could not load persisted pipeline job (%s)", type(exc).__name__)
        # The cache is only a fallback for development/test mode. In a multi-worker
        # deployment, always prefer the database row so each worker sees the latest
        # status written by whichever worker owns the job.
        return self._jobs.get(job_id)

    async def execute_job(
        self,
        job_id: str,
        audio_bytes: bytes,
        filename: str,
        language_code: str,
        patient_context: dict,
        doctor_preferred_language: str,
    ):
        """Executes pipeline asynchronously with stage progress updates."""
        job = self.get_job(job_id)
        if not job:
            return

        try:
            job.update_stage(JobStatus.PREPROCESSING, 20, "Denoising and audio normalization")
            await asyncio.sleep(0.01)

            job.update_stage(JobStatus.TRANSCRIBING, 40, "Transcribing and translating regional speech")
            await asyncio.sleep(0.01)

            job.update_stage(JobStatus.EXTRACTING, 60, "Extracting clinical symptoms and checking red flags")
            await asyncio.sleep(0.01)

            job.update_stage(JobStatus.TRIAGING, 80, "Calculating priority level and safety overrides")
            
            # Run the actual pipeline
            result = await run_pipeline(
                audio_bytes=audio_bytes,
                filename=filename,
                language_code=language_code,
                patient_context=patient_context,
                doctor_preferred_language=doctor_preferred_language,
            )

            job.result = result

            # Route by complaint category and assign to best on-duty doctor (B5.3, B5.8)
            consultation_saved = False
            try:
                from app.routing import categorize_complaint
                from app.db import save_consultation, get_default_hospital
                from app.specialty_validation import evaluate_specialty_holdout

                complaint_cat, dept_code, routing_rationale = categorize_complaint(
                    clinical_summary=result.clinical_summary,
                    patient_context=patient_context,
                    safety_screening=result.safety_screening,
                    urgency_tier=result.priority.urgency_tier,
                )

                from app.routing import DEPARTMENT_CODES
                hosp = get_default_hospital()
                specialty_gate = evaluate_specialty_holdout()
                if not specialty_gate["enabled"]:
                    dept_code = None
                doc_selection_rationale = "No clinician-labeled specialty holdout has passed 90%; nurse confirmation is required."
                assigned_doc_id = None
                doc_dict = None

                hosp_dict = {
                    "name": hosp.get("name"),
                    "code": hosp.get("code"),
                    "abdm_facility_id": hosp.get("abdm_facility_id"),
                    "facility_type": hosp.get("facility_type"),
                    "location": hosp.get("location"),
                    "emergency_phone": hosp.get("emergency_phone"),
                    "national_emergency": hosp.get("national_emergency", "112"),
                    "casualty_phone": hosp.get("casualty_phone"),
                    "opd_timings": hosp.get("opd_timings"),
                    "medical_superintendent": hosp.get("medical_superintendent"),
                    "network": hosp.get("network"),
                }

                dept_dict = {
                    "code": dept_code,
                    "name": DEPARTMENT_CODES.get(dept_code, "Manual nurse routing required") if dept_code else "Manual nurse routing required",
                    "category": complaint_cat,
                    "rationale": routing_rationale,
                    "doctor_selection_rationale": doc_selection_rationale,
                }

                wait_mins = 0 if result.priority.urgency_tier == "emergency" else (15 if result.priority.urgency_tier == "doctor_today" else 30)
                token_prefix = dept_code[:3].upper() if dept_code else "TRI"
                routing_dict = {
                    "token": f"OPD-{token_prefix}-{result.request_id[-4:].upper()}",
                    "estimated_wait_minutes": wait_mins,
                    "status": "Priority Triage Confirmed",
                }

                result.assigned_doctor = doc_dict
                result.hospital = hosp_dict
                result.department = dept_dict
                result.routing = routing_dict

                stated_name = getattr(result.clinical_summary, "stated_patient_name", None) if result.clinical_summary else None
                save_consultation(result.request_id, {
                    "hospital_id": hosp["id"],
                    "account_id": patient_context.get("account_id"),
                    "profile_id": patient_context.get("profile_id"),
                    "patient_name": patient_context.get("patient_name") or stated_name,
                    "phone_number": patient_context.get("phone_number"),
                    "age": patient_context.get("age"),
                    "gender": patient_context.get("gender"),
                    "stated_patient_name": stated_name,
                    "status": "triage",
                    "patient_language": result.patient_input.language,
                    "urgency_tier": result.priority.urgency_tier,
                    "department_id": dept_code,
                    "assigned_doctor_id": assigned_doc_id,
                    "assigned_doctor": doc_dict,
                    "hospital": hosp_dict,
                    "department": dept_dict,
                    "routing": routing_dict,
                    "original_transcript": result.patient_input.transcript_original,
                    "english_transcript": result.patient_input.transcript_english,
                    "complaint_category": complaint_cat,
                    "chief_complaint": result.clinical_summary.chief_complaint if result.clinical_summary else None,
                    "full_result": (result.model_dump() if hasattr(result, "model_dump") else result.dict()),
                    "latitude": patient_context.get("latitude"),
                    "longitude": patient_context.get("longitude"),
                })
                consultation_saved = True

            except Exception as route_err:
                if not consultation_saved:
                    raise RuntimeError("Triage result could not be saved for clinician review.") from route_err
                logger.warning("Doctor assignment failed for async job %s; saved case requires manual follow-up (%s)",
                               job_id, type(route_err).__name__)

            # Register for follow-up tracking
            try:
                from app.follow_up import get_follow_up_manager
                get_follow_up_manager().register_consultation(
                    consultation_id=result.request_id,
                    urgency_tier=result.priority.urgency_tier,
                    follow_up_days=result.priority.follow_up_days,
                )
            except Exception as fu_err:
                logger.warning("Failed to register async follow-up: %s", fu_err)

            get_metrics().record_triage(result.priority.level.value)
            get_audit_logger().log(
                action="ASYNC_JOB_COMPLETED",
                user_id="job_worker",
                role="system",
                consultation_id=result.request_id,
                details={"job_id": job_id, "priority": result.priority.level.value},
            )

            job.update_stage(JobStatus.COMPLETED, 100, "Consultation triage complete")

            # Zero-out encrypted audio payload upon completion to avoid keeping raw audio (A4/Privacy)
            try:
                from app.db import save_pipeline_job
                save_pipeline_job(job_id, {
                    "status": JobStatus.COMPLETED.value,
                    "stage": "Consultation triage complete",
                    "progress": 100,
                    "account_id": job.account_id,
                    "guest_case_id": job.guest_case_id,
                    "created_at": job.created_at,
                    "result": (job.result.model_dump() if hasattr(job.result, "model_dump") else job.result.dict()) if job.result else None,
                    "encrypted_audio_payload": None,
                })
            except Exception as clear_err:
                logger.warning("Failed to clear audio payload after completion (%s)", type(clear_err).__name__)

        except Exception as exc:
            logger.exception("Async job %s failed", job_id)
            job.status = JobStatus.FAILED
            job.error = str(exc)
            job.current_stage = "Processing failed"
            job.updated_at = time.time()
            get_metrics().record_error("AsyncJobFailure")
            try:
                job.update_stage(JobStatus.FAILED, job.progress_pct, job.current_stage)
            except Exception as persist_exc:
                logger.error("Could not persist failed pipeline job (%s)", type(persist_exc).__name__)

    async def recover_pending_jobs(self):
        """Recover in-flight jobs on worker startup across process boundaries."""
        try:
            from app.db import list_recoverable_pipeline_jobs
            import base64
            from app.security.phi_protection import decrypt_phi_field

            jobs = list_recoverable_pipeline_jobs()
            for j in jobs:
                jid = j.get("id")
                enc_audio = j.get("encrypted_audio_payload")
                if not enc_audio:
                    continue
                try:
                    decrypted_b64 = decrypt_phi_field(enc_audio)
                    audio_bytes = base64.b64decode(decrypted_b64)
                    filename = j.get("audio_filename") or "recovered_audio.wav"
                    language_code = j.get("language_code") or "unknown"
                    patient_context = j.get("patient_context") or {}
                    doctor_lang = j.get("doctor_preferred_language") or "en-IN"
                    logger.info("Recovering in-flight pipeline job %s across workers", jid)
                    asyncio.create_task(
                        self.execute_job(
                            job_id=jid,
                            audio_bytes=audio_bytes,
                            filename=filename,
                            language_code=language_code,
                            patient_context=patient_context,
                            doctor_preferred_language=doctor_lang,
                        )
                    )
                except Exception as rec_err:
                    logger.warning("Could not recover job %s: %s", jid, rec_err)
        except Exception as exc:
            logger.warning("Job recovery sweep failed (%s)", type(exc).__name__)

    def cleanup_old_jobs(self, ttl_seconds: int = 86400):
        """Purge jobs older than 24 hours."""
        now = time.time()
        expired = [jid for jid, j in self._jobs.items() if (now - j.updated_at) > ttl_seconds]
        for jid in expired:
            del self._jobs[jid]
        active_ids = set(self._jobs.keys())
        self._idempotency_keys = {k: v for k, v in self._idempotency_keys.items() if v in active_ids}


_job_manager = JobManager()


def get_job_manager() -> JobManager:
    return _job_manager
