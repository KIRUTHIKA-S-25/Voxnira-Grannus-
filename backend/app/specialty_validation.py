"""Holdout gate for deterministic complaint-to-specialty suggestions."""
import json
from pathlib import Path
from types import SimpleNamespace

HOLDOUT_PATH = Path(__file__).resolve().parent.parent / "eval" / "specialty_holdout.json"
MIN_TOP1_ACCURACY = 0.90


def evaluate_specialty_holdout():
    try:
        rows = json.loads(HOLDOUT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        rows = []
    if not isinstance(rows, list) or not rows:
        return {"enabled": False, "sample_count": 0, "accuracy": None, "threshold": MIN_TOP1_ACCURACY}

    from app.routing import categorize_complaint
    forbidden_identity_fields = {"name", "patient_name", "phone", "phone_number", "email", "address", "patient_id", "account_id", "profile_id"}
    correct = 0
    valid = 0
    for row in rows:
        if not isinstance(row, dict) or not row.get("deidentified") or not row.get("clinician_labeled") or forbidden_identity_fields.intersection(k.casefold() for k in row):
            return {"enabled": False, "sample_count": valid, "accuracy": None, "threshold": MIN_TOP1_ACCURACY}
        expected = row.get("expected_specialty")
        if not expected:
            return {"enabled": False, "sample_count": valid, "accuracy": None, "threshold": MIN_TOP1_ACCURACY}
        symptoms = [SimpleNamespace(name=str(s.get("name", "")), negated=bool(s.get("negated", False))) if isinstance(s, dict) else SimpleNamespace(name=str(s), negated=False) for s in row.get("symptoms", [])]
        summary = SimpleNamespace(chief_complaint=row.get("chief_complaint", ""), symptoms=symptoms)
        _, predicted, _ = categorize_complaint(summary, urgency_tier=row.get("urgency_tier"))
        valid += 1
        correct += int(predicted.casefold() == str(expected).casefold())
    accuracy = correct / valid if valid else None
    return {"enabled": bool(accuracy is not None and valid == len(rows) and accuracy >= MIN_TOP1_ACCURACY), "sample_count": valid, "accuracy": accuracy, "threshold": MIN_TOP1_ACCURACY}


def recommend_department(case: dict):
    """Return a rule-based department only after the clinician holdout gate passes."""
    gate = evaluate_specialty_holdout()
    if not gate["enabled"]:
        return {**gate, "department_code": None, "complaint_category": None, "eligible_doctors": []}
    from app.routing import categorize_complaint, select_best_doctor
    result = case.get("full_result") or {}
    summary_data = result.get("clinical_summary") or {}
    symptoms = [SimpleNamespace(name=str(s.get("name", "")), negated=bool(s.get("negated", False))) if isinstance(s, dict) else SimpleNamespace(name=str(s), negated=False) for s in summary_data.get("symptoms", [])]
    summary = SimpleNamespace(chief_complaint=summary_data.get("chief_complaint") or case.get("chief_complaint", ""), symptoms=symptoms)
    category, department, rationale = categorize_complaint(
        summary,
        patient_context={"age": case.get("age")},
        urgency_tier=case.get("urgency_tier"),
    )
    from app.db import get_staff_doctors
    all_doctors = get_staff_doctors(hospital_id=case.get("hospital_id"))
    recommended, doctor_rationale = select_best_doctor(
        doctors=all_doctors,
        department_code=department,
        patient_language=case.get("patient_language", "en-IN"),
    )
    return {**gate, "department_code": department, "complaint_category": category, "rationale": rationale,
            "recommended_doctor_id": (recommended or {}).get("id"), "doctor_rationale": doctor_rationale}
