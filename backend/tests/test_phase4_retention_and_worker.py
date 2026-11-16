"""
Tests for Phase 4: Audio Retention, 72-Hour Expiry, and Verified Deletion Worker.

Covers:
  1. Statutory 72-hour default retention scheduling with explicit UTC ISO-8601 timestamps.
  2. Retention sweep purging expired physical audio while permanently preserving clinical transcripts.
  3. Administrative on-demand sweep endpoint (/api/v1/admin/retention/sweep) with role-based access control.
  4. Operational retention status endpoint (/api/v1/admin/retention/status).
  5. DPDP Act 2023 Statutory Right to Erasure (/api/v1/patient/account/erasure).
  6. Invariant: Requests to stream purged/expired audio files return 404.
"""
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.auth import create_token, UserRole, generate_signed_url
from app.security.data_retention import get_retention_manager, AUDIO_DEFAULT_RETENTION_HOURS
from app.db import (
    save_consultation,
    get_consultation,
    get_deletion_logs,
)

client = TestClient(app)


def test_audio_72h_utc_timestamp_scheduling(tmp_path):
    """
    Every uploaded audio file must be scheduled with explicit UTC created_at
    and delete_after timestamps exactly 72 hours in the future by default.
    """
    test_audio = tmp_path / "patient_sample_72h.wav"
    test_audio.write_bytes(b"RIFF72h....WAVEfmt ....data....")
    file_str = str(test_audio)

    retention_mgr = get_retention_manager()
    record = retention_mgr.schedule_audio_deletion(file_str, hours=72)

    assert record["file_path"] == file_str
    assert "created_at" in record
    assert "delete_after" in record

    # Parse ISO-8601 UTC timestamps
    created_dt = datetime.fromisoformat(record["created_at"])
    delete_dt = datetime.fromisoformat(record["delete_after"])

    # Verify 72-hour delta (+/- 5 seconds buffer)
    delta = delete_dt - created_dt
    assert abs(delta.total_seconds() - (72 * 3600)) < 5


def test_periodic_sweep_purges_expired_preserves_text(tmp_path):
    """
    Advancing clock >72 hours deletes raw physical audio and logs an immutable
    deletion event, while permanently preserving clinical transcripts and summaries.
    """
    test_audio = tmp_path / "consult_to_purge.wav"
    test_audio.write_bytes(b"RIFF....WAVEfmt ....data....")
    file_str = str(test_audio)
    assert Path(file_str).exists()

    cid = "consult_h4_phase4_001"
    save_consultation(cid, {
        "id": cid,
        "transcript_original": "நெஞ்சு வலி மற்றும் மூச்சுத்திணறல்",
        "transcript_english": "Chest pain and breathlessness",
        "chief_complaint": "Acute chest pain with dyspnea",
        "urgency_tier": "emergency",
        "audio_file": file_str,
    })

    retention_mgr = get_retention_manager()
    retention_mgr.schedule_audio_deletion(file_str, hours=72)

    # Simulate clock advance past 72 hours (expiry_ts in the past)
    retention_mgr._scheduled_deletions[file_str]["expiry_ts"] = time.time() - 3600

    # Execute sweep
    purged = retention_mgr.execute_retention_sweep()
    assert file_str in purged
    assert not Path(file_str).exists()

    # Invariant: Consultation text and clinical summary remain intact
    c_after = get_consultation(cid)
    assert c_after is not None
    assert c_after["transcript_english"] == "Chest pain and breathlessness"
    assert c_after["chief_complaint"] == "Acute chest pain with dyspnea"
    assert c_after["urgency_tier"] == "emergency"

    # Verify immutable statutory audit log entry in deletion_log
    d_logs = get_deletion_logs()
    matching_log = next((log for log in d_logs if log["resource_id"] == file_str), None)
    assert matching_log is not None
    assert matching_log["resource_type"] == "audio_recording"
    assert "72-hour" in matching_log["reason"] or "retention" in matching_log["reason"]


def test_admin_retention_sweep_endpoint(tmp_path):
    """
    POST /api/v1/admin/retention/sweep requires ADMIN role and triggers an on-demand sweep.
    """
    admin_token = create_token("admin_test_01", UserRole.ADMIN)
    patient_token = create_token("pat_test_01", UserRole.PATIENT)
    doctor_token = create_token("doc_test_01", UserRole.DOCTOR, doctor_reg_no="TN123456")

    # 1. Non-admin calls are strictly forbidden
    res_patient = client.post("/api/v1/admin/retention/sweep", headers={"Authorization": f"Bearer {patient_token}"})
    assert res_patient.status_code == 403

    res_doc = client.post("/api/v1/admin/retention/sweep", headers={"Authorization": f"Bearer {doctor_token}"})
    assert res_doc.status_code == 403

    # 2. Admin call succeeds
    res_admin = client.post("/api/v1/admin/retention/sweep", headers={"Authorization": f"Bearer {admin_token}"})
    assert res_admin.status_code == 200
    data = res_admin.json()
    assert data["status"] == "success"
    assert "purged_count" in data
    assert "purged_files" in data


def test_admin_retention_status_endpoint():
    """
    GET /api/v1/admin/retention/status allows clinical staff/admin inspection of queue metrics.
    """
    admin_token = create_token("admin_test_01", UserRole.ADMIN)
    patient_token = create_token("pat_test_01", UserRole.PATIENT)

    # Patient cannot view retention status
    res_pat = client.get("/api/v1/admin/retention/status", headers={"Authorization": f"Bearer {patient_token}"})
    assert res_pat.status_code == 403

    # Admin/clinical staff can view retention metrics
    res_admin = client.get("/api/v1/admin/retention/status", headers={"Authorization": f"Bearer {admin_token}"})
    assert res_admin.status_code == 200
    data = res_admin.json()
    assert data["default_retention_hours"] == 72
    assert "scheduled_files_count" in data
    assert "purged_files_count" in data


def test_patient_dpdp_right_to_erasure():
    """
    POST /api/v1/patient/account/erasure allows patients to exercise statutory Right to Erasure
    under Section 12 of India's Digital Personal Data Protection Act 2023.
    """
    patient_acc_id = "acc_dpdp_test_99"
    patient_token = create_token("user_dpdp_01", UserRole.PATIENT, account_id=patient_acc_id)
    doctor_token = create_token("doc_dpdp_01", UserRole.DOCTOR, doctor_reg_no="TN998877")

    # Doctors cannot invoke patient account erasure
    res_doc = client.post(
        "/api/v1/patient/account/erasure",
        json={"reason": "Test non-patient caller"},
        headers={"Authorization": f"Bearer {doctor_token}"},
    )
    assert res_doc.status_code == 403

    # Patient invokes statutory erasure
    res_pat = client.post(
        "/api/v1/patient/account/erasure",
        json={"reason": "Patient requested complete account erasure under DPDP Act 2023"},
        headers={"Authorization": f"Bearer {patient_token}"},
    )
    assert res_pat.status_code == 200
    data = res_pat.json()
    assert data["status"] == "erasure_complete"
    assert data["audio_deleted"] is True
    assert data["clinical_records_anonymized"] is True
    assert "Digital Personal Data Protection Act 2023" in data["statutory_act"]

    # Verify manager status and deletion_log entry
    retention_mgr = get_retention_manager()
    assert retention_mgr.is_patient_erased(patient_acc_id)
    d_logs = get_deletion_logs()
    assert any(log["resource_type"] == "patient_account" and log["resource_id"] == patient_acc_id for log in d_logs)


def test_purged_audio_stream_returns_404(tmp_path):
    """
    H4.7: Stream request for an expired or purged audio URL returns 404 with explicit detail.
    """
    purged_path = str(tmp_path / "statutory_purged_audio.wav")
    retention_mgr = get_retention_manager()
    retention_mgr._purged_files.add(purged_path)

    signed_url = generate_signed_url(purged_path, expires_in_seconds=3600)
    res = client.get(signed_url)
    assert res.status_code == 404
    assert "expired" in res.json()["detail"].lower() or "deleted" in res.json()["detail"].lower()
