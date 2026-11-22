from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import app.db as db
import app.main as main
from app.auth import AuthenticatedUser, UserRole
from app.specialty_validation import evaluate_specialty_holdout


def staff(role: UserRole, user_id: str = "staff-1", hospital_id: str = "hospital-1", **kwargs):
    return AuthenticatedUser(user_id=user_id, role=role, hospital_id=hospital_id, **kwargs)


def test_specialty_suggestions_stay_disabled_without_clinician_holdout():
    gate = evaluate_specialty_holdout()
    assert gate["enabled"] is False
    assert gate["sample_count"] == 0
    assert gate["accuracy"] is None


def test_nurse_assignment_is_recorded_as_human_confirmation(monkeypatch):
    cid = "case-1"
    monkeypatch.setattr(db, "get_consultation", lambda _cid: {"id": cid, "hospital_id": "hospital-1"})
    monkeypatch.setattr(db, "get_default_hospital_id", lambda: "hospital-1")
    captured = {}

    def reassign(**kwargs):
        captured.update(kwargs)
        return True, "assigned", {"assigned_by": kwargs["reassigning_doctor_id"]}

    monkeypatch.setattr(db, "reassign_consultation", reassign)
    response = main.reassign_consultation_case(
        cid,
        main.ReassignCaseRequest(target_doctor_id="doctor-1", reason="Symptoms fit specialty"),
        staff(UserRole.NURSE, user_id="nurse-1"),
    )
    assert response["assignment"]["assigned_by"] == "nurse-1"
    assert captured["assigning_role"] == "nurse"
    assert captured["reason"] == "Symptoms fit specialty"


def test_doctor_cannot_document_a_case_assigned_to_another_doctor(monkeypatch):
    from app.telehealth.clinical_actions import ClinicalActionManager, DoctorActionRequest, DoctorActionType

    monkeypatch.setattr(db, "get_consultation", lambda _cid: {"id": _cid})
    monkeypatch.setattr(db, "get_assignment_for_consultation", lambda _cid: {"doctor_id": "doctor-1", "status": "assigned"})
    request = DoctorActionRequest(
        consultation_id="case-1",
        action_type=DoctorActionType.DOCUMENT_CASE,
        clinical_notes="Assessment and plan",
        disposition="handled",
    )
    with pytest.raises(HTTPException) as exc:
        ClinicalActionManager().record_action(request, staff(UserRole.DOCTOR, user_id="doctor-2", is_verified_doctor=True))
    assert exc.value.status_code == 403


def test_admin_operational_view_excludes_clinical_content_and_audits_access(monkeypatch):
    cid = "case-1"
    monkeypatch.setattr(db, "get_default_hospital_id", lambda: "hospital-1")
    monkeypatch.setattr(db, "list_consultations_for_queue", lambda **_kwargs: [{
        "id": cid, "hospital_id": "hospital-1", "patient_name": "Patient A", "account_id": "patient-1",
        "status": "triage", "urgency_tier": "doctor_today", "chief_complaint": "private symptom",
        "full_result": {"transcript": "private transcript", "clinical_notes": "secret note"},
    }])
    monkeypatch.setattr(db, "list_follow_up_records", lambda: [{
        "consultation_id": cid, "patient_status": "improving", "patient_feedback": "Thank you",
    }])
    monkeypatch.setattr(db, "get_assignment_for_consultation", lambda _cid: None)
    monkeypatch.setattr(db, "get_assignment_history", lambda _cid: [])
    monkeypatch.setattr(db, "find_staff_user", lambda _doctor_id: None)
    audited = {}
    monkeypatch.setattr(db, "append_audit_log_entry", lambda **kwargs: audited.update(kwargs))

    response = main.get_admin_operational_cases(current_user=staff(UserRole.ADMIN, user_id="admin-1"))
    serialized = str(response)
    assert "Thank you" in serialized
    assert "private transcript" not in serialized
    assert "secret note" not in serialized
    assert audited["action"] == "ADMIN_OPERATIONAL_CASES_READ"


def test_nurse_detail_contains_symptoms_but_not_transcripts(monkeypatch):
    cid = "case-1"
    monkeypatch.setattr(db, "get_consultation", lambda _cid: {
        "id": cid, "hospital_id": "hospital-1", "status": "triage", "created_at": "2026-11-22T00:00:00Z",
        "full_result": {"clinical_summary": {"symptoms": [{"name": "cough"}], "existing_conditions": ["asthma"]}, "patient_input": {"transcript_original": "private words"}},
        "original_transcript": "private words", "english_transcript": "private words",
    })
    monkeypatch.setattr(db, "get_assignment_for_consultation", lambda _cid: None)
    monkeypatch.setattr(db, "get_default_hospital_id", lambda: "hospital-1")
    monkeypatch.setattr(db, "get_default_hospital", lambda: {"id": "hospital-1", "name": "Clinic"})
    monkeypatch.setattr(db, "get_staff_doctors", lambda **_kwargs: [])
    monkeypatch.setattr(main, "get_audit_logger", lambda: SimpleNamespace(log=lambda **_kwargs: None))

    response = main.get_consultation_details(cid, staff(UserRole.NURSE, user_id="nurse-1"))
    assert response["symptom_summary"] == [{"name": "cough"}]
    assert response["relevant_history"]["existing_conditions"] == ["asthma"]
    assert "full_result" not in response
    assert "original_transcript" not in response

