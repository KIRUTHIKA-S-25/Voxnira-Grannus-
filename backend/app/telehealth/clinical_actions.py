"""
Clinician Telehealth Decision Loop and Patient Action Management for Grannus.

Supports:
  - Doctor confirmation or clinical override of AI triage priority
  - Inpatient admission, home care discharge, or secondary referral tracking
  - Audited medical decision logging under Telemedicine Practice Guidelines 2020
"""
import logging
import time
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, List, Dict, Any
from fastapi import HTTPException, status
from pydantic import BaseModel, Field

from app.auth import AuthenticatedUser
from app.security.audit_logger import get_audit_logger

logger = logging.getLogger("rural_care.clinical_actions")


class DoctorActionType(str, Enum):
    DOCUMENT_CASE = "DOCUMENT_CASE"
    CONFIRM_TRIAGE = "CONFIRM_TRIAGE"
    OVERRIDE_PRIORITY = "OVERRIDE_PRIORITY"
    PRESCRIBE_MEDICATION = "PRESCRIBE_MEDICATION"
    REFER_TO_DISTRICT_HOSPITAL = "REFER_TO_DISTRICT_HOSPITAL"
    ADMIT_TO_PHC = "ADMIT_TO_PHC"
    DISCHARGE_HOME_CARE = "DISCHARGE_HOME_CARE"


class PrescriptionItem(BaseModel):
    medication_name: str
    dosage: str
    frequency: str
    duration: str
    instructions: Optional[str] = None


class DoctorActionRequest(BaseModel):
    consultation_id: str
    action_type: DoctorActionType
    clinical_notes: str = Field(..., min_length=3, description="Doctor's clinical rationale")
    disposition: Optional[str] = Field(None, description="handled, follow_up_required, or referred")
    new_priority: Optional[str] = Field(None, description="Updated priority level if OVERRIDE_PRIORITY")
    referral_hospital: Optional[str] = Field(None, description="Name of destination facility if referred")
    referral_urgency: Optional[str] = Field(None, description="e.g. Immediate, Within 24h, Routine")
    prescriptions: Optional[List[PrescriptionItem]] = Field(default_factory=list)


class ActionRecord(BaseModel):
    action_id: str
    consultation_id: str
    doctor_id: str
    doctor_registration_number: Optional[str]
    state_medical_council: Optional[str]
    action_type: DoctorActionType
    clinical_notes: str
    disposition: Optional[str] = None
    new_priority: Optional[str]
    referral_hospital: Optional[str]
    referral_urgency: Optional[str]
    prescriptions: List[PrescriptionItem]
    timestamp: float = Field(default_factory=time.time)


class ClinicalActionManager:
    def __init__(self):
        # consultation_id -> list of ActionRecord
        self._history: Dict[str, List[ActionRecord]] = {}

    def record_action(
        self,
        req: DoctorActionRequest,
        doctor: AuthenticatedUser,
    ) -> ActionRecord:
        # 1. Enforce active claim locking: another doctor cannot act on a locked case (B5.4)
        if req.disposition and req.disposition not in {"handled", "follow_up_required", "referred"}:
            raise HTTPException(status_code=400, detail="Disposition must be handled, follow_up_required, or referred.")
        if req.action_type == DoctorActionType.DOCUMENT_CASE and not req.disposition:
            raise HTTPException(status_code=400, detail="A case disposition is required.")
        from app.db import get_consultation
        if not get_consultation(req.consultation_id):
            raise HTTPException(status_code=404, detail="Consultation not found.")
        from app.db import get_assignment_for_consultation
        assignment = get_assignment_for_consultation(req.consultation_id)
        if assignment and assignment.get("status") == "claimed":
            current_claimant = assignment.get("doctor_id")
            lock_expires_at_str = assignment.get("lock_expires_at")
            is_lock_active = True
            if lock_expires_at_str:
                try:
                    exp_dt = datetime.fromisoformat(lock_expires_at_str)
                    if exp_dt.tzinfo is None:
                        exp_dt = exp_dt.replace(tzinfo=timezone.utc)
                    if exp_dt < datetime.now(timezone.utc):
                        is_lock_active = False
                except Exception:
                    pass

            if is_lock_active and current_claimant != doctor.user_id:
                claimant_name = assignment.get("locked_by_name", "another clinician")
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Cannot record clinical action. Case is currently claimed and locked by {claimant_name}.",
                )
        if not assignment or assignment.get("doctor_id") != doctor.user_id:
            raise HTTPException(status_code=403, detail="Only the doctor assigned to this case may document clinical actions.")

        # 2. Check for duplicate submission within 10-second window (Idempotency)
        if req.consultation_id in self._history:
            for past in reversed(self._history[req.consultation_id]):
                if time.time() - past.timestamp < 10.0:
                    if (
                        past.doctor_id == doctor.user_id
                        and past.action_type == req.action_type
                        and past.clinical_notes == req.clinical_notes
                        and past.new_priority == req.new_priority
                        and past.referral_hospital == req.referral_hospital
                        and len(past.prescriptions) == len(req.prescriptions or [])
                    ):
                        past_p = [(p.medication_name, p.dosage, p.frequency, p.duration) for p in past.prescriptions]
                        req_p = [(p.medication_name, p.dosage, p.frequency, p.duration) for p in (req.prescriptions or [])]
                        if past_p == req_p:
                            logger.info(
                                "Duplicate clinical action detected within 10s for consultation %s. Returning existing action %s.",
                                req.consultation_id,
                                past.action_id,
                            )
                            return past

        # 3. Validate prescriptions if prescribing medication
        if req.action_type == DoctorActionType.PRESCRIBE_MEDICATION:
            if not req.prescriptions:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Prescription requires at least one medication item with name, dosage, frequency, and duration.",
                )

        if req.prescriptions:
            for idx, p in enumerate(req.prescriptions):
                if not p.medication_name or not p.medication_name.strip():
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Prescription item #{idx + 1} is missing a valid medication name.",
                    )
                if not p.dosage or not p.dosage.strip():
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Prescription item #{idx + 1} ({p.medication_name}) is missing dosage.",
                    )
                if not p.frequency or not p.frequency.strip():
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Prescription item #{idx + 1} ({p.medication_name}) is missing frequency.",
                    )
                if not p.duration or not p.duration.strip():
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Prescription item #{idx + 1} ({p.medication_name}) is missing duration.",
                    )

        import uuid
        action_id = f"ACT-{int(time.time() * 1000)}-{uuid.uuid4().hex[:6]}"
        record = ActionRecord(
            action_id=action_id,
            consultation_id=req.consultation_id,
            doctor_id=doctor.user_id,
            doctor_registration_number=doctor.doctor_registration_number,
            state_medical_council=doctor.state_medical_council,
            action_type=req.action_type,
            clinical_notes=req.clinical_notes,
            disposition=req.disposition,
            new_priority=req.new_priority,
            referral_hospital=req.referral_hospital,
            referral_urgency=req.referral_urgency,
            prescriptions=req.prescriptions or [],
            timestamp=time.time(),
        )

        try:
            from app.db import save_doctor_action
            payload = record.model_dump(mode="json") if hasattr(record, "model_dump") else record.dict()
            persisted = save_doctor_action(payload)
            if persisted.get("_duplicate"):
                return ActionRecord(**persisted)
        except Exception as persist_err:
            logger.error("Failed to persist clinical action (%s)", type(persist_err).__name__)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Clinical action could not be saved. Please retry.",
            ) from persist_err

        if req.consultation_id not in self._history:
            self._history[req.consultation_id] = []
        self._history[req.consultation_id].append(record)

        # Synchronize consultation state in database/in-memory records
        try:
            from app.db import get_consultation, save_consultation
            consultation = get_consultation(req.consultation_id)
            if consultation:
                if req.prescriptions:
                    p_list = [
                        p.model_dump() if hasattr(p, "model_dump") else p.dict()
                        for p in req.prescriptions
                    ]
                    consultation["prescriptions"] = p_list
                    consultation["prescribed_by"] = doctor.user_id
                    consultation["doctor_registration_number"] = doctor.doctor_registration_number
                    consultation["prescription_timestamp"] = record.timestamp

                if req.new_priority:
                    consultation["urgency_tier"] = req.new_priority.lower()

                if req.action_type == DoctorActionType.PRESCRIBE_MEDICATION:
                    consultation["status"] = "prescribed"
                elif req.action_type in (DoctorActionType.CONFIRM_TRIAGE, DoctorActionType.DISCHARGE_HOME_CARE):
                    consultation["status"] = "completed"
                elif req.action_type in (DoctorActionType.REFER_TO_DISTRICT_HOSPITAL, DoctorActionType.ADMIT_TO_PHC):
                    consultation["status"] = "referred"
                    if req.referral_hospital:
                        consultation["referral_hospital"] = req.referral_hospital
                if req.disposition:
                    consultation["status"] = {"handled": "completed", "follow_up_required": "follow_up_required", "referred": "referred"}[req.disposition]

                save_consultation(req.consultation_id, consultation)
        except Exception as sync_err:
            logger.warning("Failed to synchronize consultation record for action %s: %s", action_id, sync_err)

        get_audit_logger().log(
            action=f"CLINICAL_ACTION_{req.action_type.value}",
            user_id=doctor.user_id,
            role=doctor.role.value,
            doctor_registration_number=doctor.doctor_registration_number,
            consultation_id=req.consultation_id,
            details={
                "action_id": action_id,
                "note_character_count": len(req.clinical_notes),
                "disposition": req.disposition,
                "referral": req.referral_hospital,
                "prescriptions_count": len(req.prescriptions or []),
            },
        )
        return record

    def get_consultation_actions(self, consultation_id: str) -> List[ActionRecord]:
        try:
            from app.db import get_doctor_actions
            stored = get_doctor_actions(consultation_id)
            if stored:
                return [ActionRecord(**action) for action in stored]
        except Exception as exc:
            logger.error("Failed to read persisted clinical actions (%s)", type(exc).__name__)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Clinical action history is temporarily unavailable.",
            ) from exc
        return self._history.get(consultation_id, [])


_action_manager = ClinicalActionManager()


def get_clinical_action_manager() -> ClinicalActionManager:
    return _action_manager
