"""
Test Suite for Phase 2: Accounts, Patient Profiles, Phone OTP Hardening, and Cross-Profile Isolation.

Verifies:
  1. Cross-profile consultation isolation: Patient A cannot read Patient B's consultations via profile_id (returns 403).
  2. Recycled phone number disassociation: Resetting a recycled phone removes both profiles and disassociates consultations.
  3. Sliding-window IP rate limiting: Exceeding OTP request rate limits returns 429 Too Many Requests.
  4. Caregiver / guardian profile metadata: Profiles accurately record guardian relationships.
  5. E.164 phone normalization: Handles trunk prefix 0, international format, and whitespace.
  6. Account enumeration prevention: Identical response structure for all OTP requests.
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.auth import create_token, UserRole
from app.db import (
    save_consultation,
    get_consultation,
    get_consultations_for_patient,
    normalize_phone,
    _OTP_COOLDOWNS,
)
from app.security.rate_limiter import otp_rate_limiter

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_otp_state():
    """Ensure clean cooldowns and rate limiter state before each test."""
    _OTP_COOLDOWNS.clear()
    otp_rate_limiter.clients.clear()
    yield
    _OTP_COOLDOWNS.clear()
    otp_rate_limiter.clients.clear()


def test_cross_profile_consultation_access_denied():
    """Patient A must receive 403 when trying to access consultations of Patient B's profile."""
    # 1. Create tokens for Account A and Account B
    token_a = create_token("user_pat_a", UserRole.PATIENT, phone_number="+919876543201", account_id="acc_user_a_01")
    token_b = create_token("user_pat_b", UserRole.PATIENT, phone_number="+919876543202", account_id="acc_user_b_01")

    headers_a = {"Authorization": f"Bearer {token_a}"}
    headers_b = {"Authorization": f"Bearer {token_b}"}

    # 2. User B creates Profile B1
    res_b = client.post("/api/v1/patient/profiles", json={
        "full_name": "Patient B1",
        "age": "28",
        "gender": "female",
        "relation": "self",
        "preferred_language": "hi-IN",
    }, headers=headers_b)
    assert res_b.status_code == 200
    profile_b_id = res_b.json()["id"]

    # 3. Save a consultation under Profile B1
    cid_b = f"consult_b_{profile_b_id[:8]}"
    save_consultation(cid_b, {
        "id": cid_b,
        "account_id": "acc_user_b_01",
        "profile_id": profile_b_id,
        "patient_name": "Patient B1",
        "chief_complaint": "Acute migraine",
        "urgency_tier": "doctor_soon",
    })

    # 4. User A attempts to list consultations passing Profile B1's ID -> must return 403 Forbidden
    res_tamper = client.get(f"/api/v1/patient/consultations?profile_id={profile_b_id}", headers=headers_a)
    assert res_tamper.status_code == 403
    assert "Forbidden" in res_tamper.json()["detail"]


def test_recycled_phone_clears_prior_consultations():
    """Resetting a recycled phone number must disassociate previous consultations from the account."""
    acc_id = "acc_recycled_test_01"
    token = create_token("user_recycled", UserRole.PATIENT, phone_number="+919876543299", account_id=acc_id)
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Create profile and consultation under recycled account
    p_res = client.post("/api/v1/patient/profiles", json={
        "full_name": "Old Profile",
        "age": "50",
        "gender": "male",
        "relation": "self",
    }, headers=headers)
    assert p_res.status_code == 200
    pid = p_res.json()["id"]

    cid = "consult_recycled_01"
    save_consultation(cid, {
        "id": cid,
        "account_id": acc_id,
        "profile_id": pid,
        "chief_complaint": "Chronic knee pain",
        "urgency_tier": "self_care",
    })

    # Verify consultation is currently returned
    history_before = get_consultations_for_patient(account_id=acc_id)
    assert any(c["id"] == cid for c in history_before)

    # 2. Invoke recycled phone reset
    reset_res = client.post("/api/v1/patient/reset-recycled-phone", headers=headers)
    assert reset_res.status_code == 200

    # 3. Consultations under that account must now be empty
    history_after = get_consultations_for_patient(account_id=acc_id)
    assert len(history_after) == 0

    # Verify underlying consultation is marked disassociated
    raw_consult = get_consultation(cid)
    assert raw_consult["account_id"] is None
    assert raw_consult["recycled_disassociated"] is True


def test_otp_request_rate_limiting():
    """Multiple rapid OTP requests from the same IP must trigger 429 Too Many Requests."""
    test_phone = "+919876543210"
    ip_headers = {"x-forwarded-for": "198.51.100.55"}

    try:
        # Exhaust burst limit (burst limit is 3 requests)
        for _ in range(3):
            _OTP_COOLDOWNS.clear()  # bypass phone cooldown to specifically test IP limiter
            res = client.post("/api/v1/auth/otp/request", json={"phone_number": test_phone}, headers=ip_headers)
            assert res.status_code == 200

        # 4th burst request must be blocked by IP rate limiter
        _OTP_COOLDOWNS.clear()
        res_blocked = client.post("/api/v1/auth/otp/request", json={"phone_number": test_phone}, headers=ip_headers)
        assert res_blocked.status_code == 429
        assert "Rate limit exceeded" in res_blocked.json()["detail"]
        assert "Retry-After" in res_blocked.headers
    finally:
        otp_rate_limiter.clients.clear()
        _OTP_COOLDOWNS.clear()


def test_caregiver_profile_creation_and_attributes():
    """Verify caregiver and guardian attributes are stored on child/elderly patient profile."""
    token = create_token("user_caregiver", UserRole.PATIENT, phone_number="+919876543210", account_id="acc_caregiver_01")
    headers = {"Authorization": f"Bearer {token}"}

    res = client.post("/api/v1/patient/profiles", json={
        "full_name": "Baby Ananya",
        "age": "2",
        "gender": "female",
        "relation": "child",
        "preferred_language": "ta-IN",
        "guardian_name": "Meenakshi Sundaram",
        "is_caregiver_managed": True,
        "caregiver_relationship": "mother",
    }, headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["full_name"] == "Baby Ananya"
    assert data["guardian_name"] == "Meenakshi Sundaram"
    assert data["is_caregiver_managed"] is True
    assert data["caregiver_relationship"] == "mother"


def test_e164_trunk_prefix_normalization():
    """Verify normalize_phone handles trunk prefix 0 and whitespace cleanly."""
    assert normalize_phone("09876543210") == "+919876543210"
    assert normalize_phone("+91 98765 43210") == "+919876543210"
    assert normalize_phone("9876543210") == "+919876543210"
    assert normalize_phone("+91-98765-43210") == "+919876543210"


def test_consistent_otp_response_prevents_enumeration():
    """OTP requests return uniform responses without disclosing if an account pre-exists."""
    res1 = client.post("/api/v1/auth/otp/request", json={"phone_number": "+919811122233"})
    assert res1.status_code == 200
    data1 = res1.json()

    _OTP_COOLDOWNS.clear()
    otp_rate_limiter.clients.clear()

    res2 = client.post("/api/v1/auth/otp/request", json={"phone_number": "+919822233344"})
    assert res2.status_code == 200
    data2 = res2.json()

    # Invariant: Responses must be consistent and never leak OTP codes or user existence
    assert data1["success"] == data2["success"]
    assert "Verification code" in data1["message"]
    assert "Verification code" in data2["message"]
    assert "code" not in data1
    assert "code" not in data2
    otp_rate_limiter.clients.clear()

