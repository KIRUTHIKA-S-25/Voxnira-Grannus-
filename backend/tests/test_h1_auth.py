"""
Acceptance tests for Block H1: Authentication & Access Control Hardening.
Verifies:
  - H1.1: Removal of open login (legacy user_id + role bypass returns 401)
  - H1.2: SMS OTP routes stay disabled; phone + PIN is the patient sign-in path
  - H1.3: Emergency access without OTP (guest emergency token reads only its own case, 403 on other cases or lists)
  - H1.4: Session token refresh and role enforcement
"""
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.auth import create_token, UserRole
from app.db import save_consultation

client = TestClient(app)


def test_h1_open_login_is_rejected():
    """H1.1: POST /api/v1/auth/login with user_id + role and no password must return 401."""
    res = client.post("/api/v1/auth/login", json={"user_id": "attacker_user", "role": "admin"})
    assert res.status_code == 401
    assert "Invalid credentials" in res.json().get("detail", "") or "Password is required" in res.json().get("detail", "")


def test_h1_unregistered_staff_with_password_is_rejected():
    """H1.1: Random unknown email and password must return 401."""
    res = client.post("/api/v1/auth/login", json={
        "email": "hacker@fakehospital.in",
        "password": "random_password_123",
        "role": "doctor",
    })
    assert res.status_code == 401


def test_h1_sms_otp_routes_are_disabled():
    """SMS sign-in is retired and direct PIN login remains the patient path."""
    res = client.post("/api/v1/auth/otp/request", json={"phone_number": "+919876543210"})
    assert res.status_code == 410
    assert "PIN" in res.json()["detail"]
    verify = client.post("/api/v1/auth/otp/verify", json={"phone_number": "+919876543210", "otp_code": "123456"})
    assert verify.status_code == 410


def test_h1_otp_verification_never_accepts_codes():
    """No OTP code can authenticate without the disabled SMS delivery flow."""
    test_phone = "+919123456789"
    # Request OTP
    req_res = client.post("/api/v1/auth/otp/request", json={"phone_number": test_phone})
    assert req_res.status_code == 410

    # Attempt 5 wrong codes
    for _ in range(5):
        client.post("/api/v1/auth/otp/verify", json={"phone_number": test_phone, "otp_code": "000000"})

    # Every attempt is rejected as a retired route.
    final_res = client.post("/api/v1/auth/otp/verify", json={"phone_number": test_phone, "otp_code": "000000"})
    assert final_res.status_code == 410


def test_h1_guest_emergency_access_without_otp():
    """H1.3: Guest emergency intake produces a single-case token that reads only its own case."""
    # 1. Create guest emergency case
    res = client.post("/api/v1/auth/guest-emergency", json={
        "guest_name": "Senthil Kumar",
        "age": "62",
    })
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    guest_token = data["guest_token"]
    case_id = data["case_id"]
    headers = {"Authorization": f"Bearer {guest_token}"}

    # 2. Guest can read its own emergency case
    case_res = client.get(f"/api/v1/consultations/{case_id}", headers=headers)
    assert case_res.status_code == 200
    assert case_res.json()["id"] == case_id

    # 3. Guest CANNOT read other consultations
    other_cid = "other_consultation_secret_999"
    save_consultation(other_cid, {
        "id": other_cid,
        "patient_id": "other_patient_111",
        "urgency_tier": "doctor_today",
        "chief_complaint": "Confidential patient case",
    })
    forbidden_res = client.get(f"/api/v1/consultations/{other_cid}", headers=headers)
    assert forbidden_res.status_code == 403

    # 4. Guest CANNOT browse patient history or profile lists
    history_res = client.get("/api/v1/patient/consultations", headers=headers)
    assert history_res.status_code == 403

    profiles_res = client.get("/api/v1/patient/profiles", headers=headers)
    assert profiles_res.status_code == 403


def test_h1_session_token_refresh():
    """H1.4: Active token can be refreshed."""
    token = create_token(
        user_id="pat_session_test",
        role=UserRole.PATIENT,
        account_id="acc_test_refresh",
        phone_number="+919876543210",
    )
    res = client.post("/api/v1/auth/refresh", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    data = res.json()
    assert "token" in data
    assert data["user_id"] == "pat_session_test"
