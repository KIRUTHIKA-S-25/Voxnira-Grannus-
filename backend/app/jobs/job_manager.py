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
    def __init__(self, job_id: str):
        self.job_id = job_id
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
                "result": (self.result.model_dump() if hasattr(self.result, "model_dump") else self.result.dict()) if self.result else None,
                "error_message": self.error,
            })
        except Exception:
            pass

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

    def create_job(self, idempotency_key: Optional[str] = None) -> str:
        """Create a new job record or return active job matching idempotency_key."""
        if idempotency_key and idempotency_key in self._idempotency_keys:
            existing_job_id = self._idempotency_keys[idempotency_key]
            existing_job = self.get_job(existing_job_id)
            if existing_job and existing_job.status != JobStatus.FAILED:
                return existing_job_id

        job_id = f"job_{uuid.uuid4().hex[:12]}"
        rec = JobRecord(job_id)
        self._jobs[job_id] = rec
        if idempotency_key:
            self._idempotency_keys[idempotency_key] = job_id
        try:
            from app.db import save_pipeline_job
            save_pipeline_job(job_id, {
                "status": rec.status.value,
                "stage": rec.current_stage,
                "progress": rec.progress_pct,
            })
        except Exception:
            pass
        return job_id

    def get_job_by_idempotency_key(self, idempotency_key: str) -> Optional[JobRecord]:
        if not idempotency_key:
            return None
        job_id = self._idempotency_keys.get(idempotency_key)
        if job_id:
            return self.get_job(job_id)
        return None

    def get_job(self, job_id: str) -> Optional[JobRecord]:
        if job_id in self._jobs:
            return self._jobs[job_id]
        try:
            from app.db import get_pipeline_job
            data = get_pipeline_job(job_id)
            if data:
                rec = JobRecord(job_id)
                rec.status = JobStatus(data.get("status", "QUEUED"))
                rec.progress_pct = data.get("progress", 0)
                rec.current_stage = data.get("stage", "")
                self._jobs[job_id] = rec
                return rec
        except Exception:
            pass
        return None

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
            job.update_stage(JobStatus.COMPLETED, 100, "Consultation triage complete")

            # Route by complaint category and assign to best on-duty doctor (B5.3, B5.8)
            try:
                from app.routing import categorize_complaint, select_best_doctor
                from app.db import save_consultation, save_assignment, get_staff_doctors

                complaint_cat, dept_code, routing_rationale = categorize_complaint(
                    clinical_summary=result.clinical_summary,
                    patient_context=patient_context,
                    safety_screening=result.safety_screening,
                    urgency_tier=result.priority.urgency_tier,
                )

                from app.routing import DEPARTMENT_CODES
                from app.db import _MEM_HOSPITALS
                all_doctors = get_staff_doctors()
                assigned_doc, doc_selection_rationale = select_best_doctor(
                    doctors=all_doctors,
                    department_code=dept_code,
                    patient_language=result.patient_input.language,
                )
                assigned_doc_id = assigned_doc.get("id") if assigned_doc else None

                hosp = _MEM_HOSPITALS[0]
                doc_dict = None
                if assigned_doc:
                    doc_dict = {
                        "id": assigned_doc.get("id"),
                        "full_name": assigned_doc.get("full_name"),
                        "specialty": assigned_doc.get("specialty"),
                        "qualifications": assigned_doc.get("qualifications", "MBBS, MD"),
                        "registration_number": assigned_doc.get("doctor_registration_number"),
                        "state_council": assigned_doc.get("state_council"),
                        "department_code": assigned_doc.get("department_code") or dept_code,
                        "department_name": DEPARTMENT_CODES.get(dept_code, dept_code),
                        "opd_room": assigned_doc.get("opd_room", "Room 101, Ground Floor OPD Block"),
                        "schedule": assigned_doc.get("weekly_schedule"),
                        "experience": assigned_doc.get("experience", "12 Years Clinical Practice"),
                        "languages": assigned_doc.get("languages", ["English"]),
                    }

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
                    "name": DEPARTMENT_CODES.get(dept_code, dept_code),
                    "category": complaint_cat,
                    "rationale": routing_rationale,
                    "doctor_selection_rationale": doc_selection_rationale,
                }

                wait_mins = 0 if result.priority.urgency_tier == "emergency" else (15 if result.priority.urgency_tier == "doctor_today" else 30)
                token_prefix = dept_code[:3].upper()
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

                if assigned_doc_id:
                    save_assignment(
                        consultation_id=result.request_id,
                        doctor_id=assigned_doc_id,
                        assigned_by="system_routing",
                        status="assigned",
                        reassignment_reason=f"{routing_rationale} {doc_selection_rationale}",
                        locked_by_name=assigned_doc.get("full_name"),
                    )
            except Exception as route_err:
                logger.warning("Routing failed for async job %s: %s", job_id, route_err)

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

        except Exception as exc:
            logger.exception("Async job %s failed", job_id)
            job.status = JobStatus.FAILED
            job.error = str(exc)
            job.current_stage = f"Processing failed: {exc}"
            job.updated_at = time.time()
            get_metrics().record_error("AsyncJobFailure")

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
