"""Idempotently encrypt supported legacy plaintext PHI fields in Supabase.

Run from the backend directory with production SUPABASE_URL,
SUPABASE_SERVICE_ROLE_KEY, and PHI_ENCRYPTION_KEY set. This script logs only
table names and row counts; it never emits patient values.
"""
import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Callable

# Ensure backend root is on sys.path
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from supabase import create_client

from app.config import get_settings
from app.db import (
    _encode_account_row,
    _encode_consultation_row,
    _encode_json_phi,
    _encode_otp_row,
    _encode_profile_row,
    _encode_text_fields,
    _encode_encrypted_json,
)
from app.security.phi_protection import decrypt_phi_field, encrypt_phi_field, phi_lookup_hash

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("phi_backfill")
BATCH_SIZE = 200


def _account(row: dict) -> dict:
    protected = _encode_account_row(row)
    return {k: protected[k] for k in ("phone_number", "phone_lookup_hash") if protected.get(k) != row.get(k)}


def _otp(row: dict) -> dict:
    protected = _encode_otp_row(row)
    return {k: protected[k] for k in ("phone_number", "phone_lookup_hash") if protected.get(k) != row.get(k)}


def _profile(row: dict) -> dict:
    protected = _encode_profile_row(row)
    fields = ("full_name", "age", "gender", "guardian_name", "allergies", "medications", "known_conditions")
    return {k: protected[k] for k in fields if protected.get(k) != row.get(k)}


def _consultation(row: dict) -> dict:
    protected = _encode_consultation_row(row)
    fields = (
        "original_transcript", "english_transcript", "chief_complaint", "complaint_category",
        "audio_url", "full_result", "prescriptions",
    )
    return {k: protected.get(k) for k in fields if protected.get(k) != row.get(k)}


def _pipeline_job(row: dict) -> dict:
    changes = {}
    result = _encode_json_phi(row.get("result"))
    if result != row.get("result"):
        changes["result"] = result
    error = _encode_text_fields(row, ("error_message",)).get("error_message")
    if error != row.get("error_message"):
        changes["error_message"] = error
    message = _encode_text_fields(row, ("message",)).get("message")
    if message != row.get("message"):
        changes["message"] = message
    return changes


def _voice_thread(row: dict) -> dict:
    value = _encode_json_phi(row.get("messages"))
    return {"messages": value} if value != row.get("messages") else {}


def _follow_up(row: dict) -> dict:
    protected = _encode_text_fields(row, ("patient_status", "patient_feedback"))
    return {k: protected[k] for k in ("patient_status", "patient_feedback") if protected.get(k) != row.get(k)}


def _audit(row: dict) -> dict:
    changes = {}
    value = _encode_json_phi(row.get("details"))
    if value != row.get("details"):
        changes["details"] = value
    ip_address = _encode_text_fields(row, ("ip_address",)).get("ip_address")
    if ip_address != row.get("ip_address"):
        changes["ip_address"] = ip_address
    return changes


def _guest_case(row: dict) -> dict:
    fields = ("patient_name", "chief_complaint", "urgency_tier")
    protected = _encode_text_fields(row, fields)
    changes = {k: protected[k] for k in fields if protected.get(k) != row.get(k)}
    if row.get("age") is not None and not row.get("age_ciphertext"):
        changes["age_ciphertext"] = encrypt_phi_field(str(row["age"]))
        changes["age"] = None
    coords = {k: row.get(k) for k in ("latitude", "longitude") if row.get(k) is not None}
    if coords and not row.get("location_ciphertext"):
        changes["location_ciphertext"] = _encode_encrypted_json(coords)
        changes.update({key: None for key in coords})
    return changes


def _sos(row: dict) -> dict:
    fields = ("patient_name", "patient_phone", "contact_phone", "notes", "emergency_type")
    protected = _encode_text_fields(row, fields)
    changes = {k: protected[k] for k in fields if protected.get(k) != row.get(k)}
    coords = {k: row.get(k) for k in ("latitude", "longitude", "accuracy") if row.get(k) is not None}
    if coords and not row.get("location_ciphertext"):
        changes["location_ciphertext"] = _encode_encrypted_json(coords)
        changes.update({key: None for key in coords})
    return changes


def _audio_retention(row: dict) -> dict:
    path = row.get("file_path") or ""
    plaintext_path = decrypt_phi_field(path) if path.startswith("v2:") else path
    digest = phi_lookup_hash(plaintext_path, "audio-file-path") if plaintext_path else ""
    changes = {}
    if path and not path.startswith("v2:"):
        changes["file_path"] = encrypt_phi_field(path)
    if digest and digest != row.get("file_path_lookup_hash"):
        changes["file_path_lookup_hash"] = digest
    metadata = _encode_json_phi(row.get("metadata"))
    if metadata != row.get("metadata"):
        changes["metadata"] = metadata
    return changes


def _assignment(row: dict) -> dict:
    protected = _encode_text_fields(row, ("reassignment_reason", "locked_by_name"))
    return {k: protected[k] for k in ("reassignment_reason", "locked_by_name") if protected.get(k) != row.get(k)}


def _deletion_log(row: dict) -> dict:
    changes = {}
    protected = _encode_text_fields(row, ("resource_id", "reason"))
    for key in ("resource_id", "reason"):
        if protected.get(key) != row.get(key):
            changes[key] = protected[key]
    metadata = _encode_json_phi(row.get("metadata"))
    if metadata != row.get("metadata"):
        changes["metadata"] = metadata
    return changes


def _clinical_summary(row: dict) -> dict:
    changes = {}
    protected = _encode_text_fields(row, ("chief_complaint", "relevant_history", "extraction_notes"))
    for key in ("chief_complaint", "relevant_history", "extraction_notes"):
        if protected.get(key) != row.get(key):
            changes[key] = protected[key]
    for key in ("doctor_translated_summary", "home_remedy_guidance"):
        value = _encode_json_phi(row.get(key))
        if value != row.get(key):
            changes[key] = value
    return changes


def _pipeline_result(row: dict) -> dict:
    changes = {}
    protected = _encode_text_fields(row, ("chief_complaint",))
    if protected.get("chief_complaint") != row.get("chief_complaint"):
        changes["chief_complaint"] = protected["chief_complaint"]
    value = _encode_json_phi(row.get("full_result"))
    if value != row.get("full_result"):
        changes["full_result"] = value
    return changes


def _follow_up_question(row: dict) -> dict:
    protected = _encode_text_fields(row, ("english_question", "translated_question", "patient_answer"))
    return {
        key: protected[key]
        for key in ("english_question", "translated_question", "patient_answer")
        if protected.get(key) != row.get(key)
    }


def _consent(row: dict) -> dict:
    value = _encode_text_fields(row, ("ip_address",)).get("ip_address")
    return {"ip_address": value} if value != row.get("ip_address") else {}


def _profile_legacy(row: dict) -> dict:
    protected = _encode_text_fields(row, ("full_name", "phone_number"))
    return {key: protected[key] for key in ("full_name", "phone_number") if protected.get(key) != row.get(key)}


def _array_fields(row: dict, fields: tuple[str, ...]) -> dict:
    changes = {}
    for field in fields:
        values = row.get(field)
        if isinstance(values, list):
            protected = [encrypt_phi_field(str(value)) if value and not str(value).startswith("v2:") else value for value in values]
            if protected != values:
                changes[field] = protected
    return changes


def _patient_legacy(row: dict) -> dict:
    sensitive = {key: row.get(key) for key in ("age", "gender", "known_conditions", "current_medications", "allergies") if row.get(key) is not None}
    if not sensitive or row.get("phi_ciphertext"):
        return {}
    changes = {"phi_ciphertext": _encode_encrypted_json(sensitive)}
    changes.update({key: None for key in sensitive})
    return changes


def _location(row: dict) -> dict:
    sensitive = {key: row.get(key) for key in ("latitude", "longitude", "address_text") if row.get(key) is not None}
    if not sensitive or row.get("location_ciphertext"):
        return {}
    changes = {"location_ciphertext": _encode_encrypted_json(sensitive)}
    changes.update({key: None for key in sensitive})
    return changes


def _priority_assessment(row: dict) -> dict:
    return _array_fields(row, ("reasons",))


def _symptom(row: dict) -> dict:
    protected = _encode_text_fields(row, ("name", "body_location"))
    return {key: protected[key] for key in ("name", "body_location") if protected.get(key) != row.get(key)}


def _doctor_action(row: dict) -> dict:
    changes = {}
    details = _encode_json_phi(row.get("details"))
    if details != row.get("details"):
        changes["details"] = details
    notes = _encode_text_fields(row, ("notes",)).get("notes")
    if notes != row.get("notes"):
        changes["notes"] = notes
    return changes


TABLES: dict[str, Callable[[dict], dict]] = {
    "accounts": _account,
    "otp_verifications": _otp,
    "patient_profiles": _profile,
    "profiles": _profile_legacy,
    "patients": _patient_legacy,
    "locations": _location,
    "consents": _consent,
    "consultations": _consultation,
    "clinical_summaries": _clinical_summary,
    "pipeline_results": _pipeline_result,
    "symptoms": _symptom,
    "priority_assessments": _priority_assessment,
    "follow_up_questions": _follow_up_question,
    "pipeline_jobs": _pipeline_job,
    "persistent_voice_threads": _voice_thread,
    "persistent_follow_ups": _follow_up,
    "audit_logs": _audit,
    "guest_cases": _guest_case,
    "emergency_sos_dispatches": _sos,
    "audio_retention_queue": _audio_retention,
    "consultation_assignments": _assignment,
    "deletion_log": _deletion_log,
    "doctor_actions": _doctor_action,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Count rows that would change without writing them")
    args = parser.parse_args()

    settings = get_settings()
    service_key = settings.supabase_service_role_key or os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    if not settings.supabase_url or not service_key:
        parser.error("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be configured")
    if len(settings.phi_encryption_key) < 32:
        parser.error("PHI_ENCRYPTION_KEY must contain at least 32 characters")

    client = create_client(settings.supabase_url, service_key)
    for table, encoder in TABLES.items():
        changed = 0
        offset = 0
        while True:
            response = (client.table(table).select("*").order("id")
                        .range(offset, offset + BATCH_SIZE - 1).execute())
            rows = response.data or []
            if not rows:
                break
            for row in rows:
                changes = encoder(row)
                if not changes:
                    continue
                if not args.dry_run:
                    try:
                        client.table(table).update(changes).eq("id", row["id"]).execute()
                    except Exception as exc:
                        if "immutable" in str(exc).lower():
                            logger.info("Skipping immutable %s row: %s", table, exc)
                            continue
                        raise
                changed += 1
            offset += len(rows)
            if len(rows) < BATCH_SIZE:
                break
        logger.info("%s: %d row(s) %s", table, changed, "would be updated" if args.dry_run else "updated")

    logger.info("Backfill pass complete; rerun until every table reports zero changed rows.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
