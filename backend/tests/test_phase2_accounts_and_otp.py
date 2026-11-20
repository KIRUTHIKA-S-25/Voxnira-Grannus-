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


def test_patient_phone_status_and_pin_registration():
    """Verify patient phone status check and compulsory 4-digit PIN registration."""
    new_phone = "+919777888999"

    # 1. New phone has no PIN set
    status_res = client.get(f"/api/v1/auth/patient/phone-status?phone_number={new_phone}")
    assert status_res.status_code == 200
    status_data = status_res.json()
    assert status_data["exists"] is False
    assert status_data["has_pin"] is False
    assert status_data["is_locked"] is False

    # 2. Reject non-4-digit PINs
    bad_res1 = client.post("/api/v1/auth/patient/set-pin", json={"phone_number": new_phone, "pin": "12"})
    assert bad_res1.status_code == 400
    assert "PIN must be exactly 4 numeric digits" in bad_res1.json()["detail"]

    bad_res2 = client.post("/api/v1/auth/patient/set-pin", json={"phone_number": new_phone, "pin": "12345"})
    assert bad_res2.status_code == 400

    bad_res3 = client.post("/api/v1/auth/patient/set-pin", json={"phone_number": new_phone, "pin": "abcd"})
    assert bad_res3.status_code == 400

    # 3. Successfully register 4-digit PIN
    set_res = client.post("/api/v1/auth/patient/set-pin", json={"phone_number": new_phone, "pin": "8520"})
    assert set_res.status_code == 200
    auth_data = set_res.json()
    assert auth_data["role"] == "patient"
    assert auth_data["phone_number"] == new_phone
    assert "token" in auth_data
    assert "grannus_auth_token" in set_res.cookies

    # 4. Status now reflects registered PIN
    status_res2 = client.get(f"/api/v1/auth/patient/phone-status?phone_number={new_phone}")
    assert status_res2.status_code == 200
    assert status_res2.json()["exists"] is True
    assert status_res2.json()["has_pin"] is True

    # 5. Cannot overwrite PIN through set-pin if already registered
    dup_res = client.post("/api/v1/auth/patient/set-pin", json={"phone_number": new_phone, "pin": "9999"})
    assert dup_res.status_code == 400
    assert "already configured" in dup_res.json()["detail"]


def test_patient_pin_verification_and_brute_force_lockout():
    """Verify returning patient login with PIN and 5-attempt lockout enforcement."""
    phone = "+919555666777"
    valid_pin = "3690"

    # Register initial PIN
    reg_res = client.post("/api/v1/auth/patient/set-pin", json={"phone_number": phone, "pin": valid_pin})
    assert reg_res.status_code == 200

    # 1. Incorrect PIN attempts decrement attempts remaining
    for i in range(1, 5):
        wrong_res = client.post("/api/v1/auth/patient/login", json={"phone_number": phone, "pin": "0000"})
        assert wrong_res.status_code == 401
        expected_remaining = 5 - i
        assert f"{expected_remaining} attempt(s) remaining" in wrong_res.json()["detail"]

    # 2. 5th failed attempt locks the account
    lock_res = client.post("/api/v1/auth/patient/login", json={"phone_number": phone, "pin": "0000"})
    assert lock_res.status_code == 401
    assert "locked for 15 minutes" in lock_res.json()["detail"]

    # 3. Subsequent attempt during lockout is refused even with correct PIN
    refused_res = client.post("/api/v1/auth/patient/login", json={"phone_number": phone, "pin": valid_pin})
    assert refused_res.status_code == 401
    assert "temporarily locked" in refused_res.json()["detail"]

    # 4. Phone status indicates lockout
    status_res = client.get(f"/api/v1/auth/patient/phone-status?phone_number={phone}")
    assert status_res.status_code == 200
    assert status_res.json()["is_locked"] is True
    assert status_res.json()["remaining_lockout_seconds"] > 0


def test_patient_pin_login_successful_and_loads_profile():
    """Verify successful 4-digit PIN login and access to patient profiles."""
    phone = "+919444333222"
    pin = "1357"

    reg_res = client.post("/api/v1/auth/patient/set-pin", json={"phone_number": phone, "pin": pin})
    assert reg_res.status_code == 200

    login_res = client.post("/api/v1/auth/patient/login", json={"phone_number": phone, "pin": pin})
    assert login_res.status_code == 200
    token = login_res.json()["token"]
    assert "grannus_auth_token" in login_res.cookies

    # Access profiles with issued token
    headers = {"Authorization": f"Bearer {token}"}
    prof_res = client.get("/api/v1/patient/profiles", headers=headers)
    assert prof_res.status_code == 200
    profiles = prof_res.json()
    assert len(profiles) >= 1
    assert profiles[0]["relation"] == "self"


