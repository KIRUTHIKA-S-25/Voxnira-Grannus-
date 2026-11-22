"""
Database persistence and multi-tenancy layer for Grannus RuralCare AI.

Provides persistent access to:
  - Hospitals & Departments (Multi-tenancy)
  - Phone Accounts & PIN Sign-In (B2.2)
  - Patient Profiles (Multi-profile per phone B4.1)
  - Staff Users (Doctors, Nurses, Admins B2.3)
  - Pipeline Jobs (Persistent job queue B3.1)
  - Voice Threads (Persistent 2-way threads B3.1)
  - Follow-up Records (Persistent check-ins B3.1)
  - Audit Log (Append-only security log B3.5)
"""
import os
import re
import hmac
import hashlib
import logging
import json
import random
import time
import uuid
import threading
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any, Tuple

from app.config import get_settings

logger = logging.getLogger("rural_care.db")

_supabase_client = None


def _handle_database_error(operation: str, exc: Exception) -> None:
    """Log database failures without PHI and prevent production memory fallback."""
    logger.error("Supabase operation %s failed (%s)", operation, type(exc).__name__)
    if get_settings().is_production:
        raise RuntimeError(f"Database operation '{operation}' failed; refusing to use volatile fallback storage.") from exc


def get_supabase_client():
    """Lazy initialize Supabase client with server-side service-role key (H2.1, H2.2)."""
    global _supabase_client
    if _supabase_client is not None:
        return _supabase_client

    settings = get_settings()
    is_production = settings.is_production
    if settings.env.lower() == "test":
        return None
    key = settings.supabase_service_role_key or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not is_production and not key:
        key = settings.supabase_anon_key
    if settings.supabase_url and key:
        try:
            from supabase import create_client, Client
            _supabase_client = create_client(settings.supabase_url, key)
            logger.info("Supabase client initialized successfully")
        except Exception as exc:
            if settings.is_production:
                raise RuntimeError(f"Database connection failed in {settings.env}: {exc}")
            logger.warning("Could not initialize Supabase client: %s. Using in-memory fallback store.", exc)
            _supabase_client = None
    elif settings.is_production:
        raise RuntimeError(f"Database configuration missing in {settings.env}. SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are mandatory.")
    return _supabase_client


# -----------------------------------------------------------------------------
# In-Memory Fallback Stores (used if DB connection fails or during unit tests)
# -----------------------------------------------------------------------------
_MEM_HOSPITALS = [
    {
        "id": "c5b971d1-fe39-40bf-a5cb-539f1a98059f",
        "name": "Government District Headquarters Hospital & Telemedicine Hub",
        "code": "GDHH-TN-401",
        "abdm_facility_id": "IN3310004928",
        "facility_type": "District Headquarters Hospital (Tertiary Referral Center)",
        "location": "Main Hospital Road, Kallakurichi District, Tamil Nadu 606202",
        "emergency_phone": "108",
        "national_emergency": "112",
        "casualty_phone": "+91 4151 222108",
        "opd_timings": "Mon–Sat 08:00 AM – 04:00 PM (24x7 Casualty & Trauma Resuscitation)",
        "telemedicine_node_id": "TELE-TN-D01",
        "medical_superintendent": "Dr. K. S. Rajasekharan, MS, MCh",
        "network": "National Health Mission (NHM) • ABDM Unified Health Interface",
    }
]

_MEM_DEPARTMENTS = [
    {"id": "d1", "hospital_id": "c5b971d1-fe39-40bf-a5cb-539f1a98059f", "name": "Emergency Medicine & Trauma", "code": "EMERGENCY"},
    {"id": "d2", "hospital_id": "c5b971d1-fe39-40bf-a5cb-539f1a98059f", "name": "General Medicine & NCDs", "code": "GEN_MED"},
    {"id": "d3", "hospital_id": "c5b971d1-fe39-40bf-a5cb-539f1a98059f", "name": "Pediatrics & Neonatology", "code": "PEDIATRICS"},
    {"id": "d4", "hospital_id": "c5b971d1-fe39-40bf-a5cb-539f1a98059f", "name": "Obstetrics & Gynecology (Maternal Care)", "code": "OBGYN"},
    {"id": "d5", "hospital_id": "c5b971d1-fe39-40bf-a5cb-539f1a98059f", "name": "Cardiology & Vascular Medicine", "code": "CARDIOLOGY"},
    {"id": "d6", "hospital_id": "c5b971d1-fe39-40bf-a5cb-539f1a98059f", "name": "Orthopaedics & Joint Surgery", "code": "ORTHOPEDICS"},
    {"id": "d7", "hospital_id": "c5b971d1-fe39-40bf-a5cb-539f1a98059f", "name": "Pulmonology & Respiratory Medicine", "code": "PULMONOLOGY"},
    {"id": "d8", "hospital_id": "c5b971d1-fe39-40bf-a5cb-539f1a98059f", "name": "Neurology & Acute Stroke Unit", "code": "NEUROLOGY"},
    {"id": "d9", "hospital_id": "c5b971d1-fe39-40bf-a5cb-539f1a98059f", "name": "Gastroenterology & Hepatology", "code": "GASTROENTEROLOGY"},
    {"id": "d10", "hospital_id": "c5b971d1-fe39-40bf-a5cb-539f1a98059f", "name": "Dermatology & Cutaneous Medicine", "code": "DERMATOLOGY"},
    {"id": "d11", "hospital_id": "c5b971d1-fe39-40bf-a5cb-539f1a98059f", "name": "ENT (Otorhinolaryngology)", "code": "ENT"},
    {"id": "d12", "hospital_id": "c5b971d1-fe39-40bf-a5cb-539f1a98059f", "name": "Ophthalmology (Eye Care)", "code": "OPHTHALMOLOGY"},
]

_MEM_ACCOUNTS: Dict[str, dict] = {}
_MEM_OTPS: Dict[str, list] = {}
_MEM_PROFILES: Dict[str, dict] = {}
_MEM_STAFF: Dict[str, dict] = {}
_MEM_JOBS: Dict[str, dict] = {}
_MEM_THREADS: Dict[str, dict] = {}
_MEM_FOLLOW_UPS: Dict[str, dict] = {}
_MEM_CONSULTATIONS: Dict[str, dict] = {}
_MEM_ASSIGNMENTS: Dict[str, dict] = {}
_MEM_AUDIT_LOGS: List[dict] = []
_MEM_GUEST_CASES: Dict[str, dict] = {}
_MEM_DELETION_LOGS: List[dict] = []

# Rate limiter for OTP requests: phone -> timestamp
_OTP_COOLDOWNS: Dict[str, float] = {}


def _phone_lookup_hash(phone: str) -> str:
    from app.security.phi_protection import phi_lookup_hash
    return phi_lookup_hash(phone, "patient-phone")


def _decode_account_row(row: Optional[dict]) -> Optional[dict]:
    if not row:
        return row
    record = dict(row)
    phone = record.get("phone_number") or ""
    if phone.startswith("v2:"):
        from app.security.phi_protection import decrypt_phi_field
        record["phone_number"] = decrypt_phi_field(phone)
    return record


def _encode_account_row(row: dict) -> dict:
    record = dict(row)
    phone = record.get("phone_number") or ""
    if phone and not phone.startswith("v2:"):
        from app.security.phi_protection import encrypt_phi_field
        record["phone_number"] = encrypt_phi_field(phone)
    record["phone_lookup_hash"] = _phone_lookup_hash(phone if not phone.startswith("v2:") else _decode_account_row({"phone_number": phone})["phone_number"])
    return record


def _encode_otp_row(row: dict) -> dict:
    record = dict(row)
    phone = record.get("phone_number") or ""
    from app.security.phi_protection import encrypt_phi_field
    plaintext_phone = phone
    if phone.startswith("v2:"):
        from app.security.phi_protection import decrypt_phi_field
        plaintext_phone = decrypt_phi_field(phone)
    record["phone_lookup_hash"] = _phone_lookup_hash(plaintext_phone)
    if phone and not phone.startswith("v2:"):
        record["phone_number"] = encrypt_phi_field(phone)
    return record


def _load_account_by_phone(client, phone: str) -> Optional[dict]:
    """Lookup by keyed digest; lazily migrate legacy plaintext phone rows."""
    lookup = _phone_lookup_hash(phone)
    res = (client.table("accounts").select("*")
           .eq("phone_lookup_hash", lookup).eq("is_active", True).limit(1).execute())
    if res.data:
        return _decode_account_row(res.data[0])
    legacy = (client.table("accounts").select("*")
              .eq("phone_number", phone).eq("is_active", True).limit(1).execute())
    if not legacy.data:
        return None
    row = legacy.data[0]
    protected = _encode_account_row(row)
    client.table("accounts").update({
        "phone_number": protected["phone_number"],
        "phone_lookup_hash": protected["phone_lookup_hash"],
    }).eq("id", row["id"]).execute()
    return _decode_account_row(protected)


def _migrate_account_row(client, row: dict) -> dict:
    protected = _encode_account_row(row)
    if protected.get("phone_number") != row.get("phone_number") or protected.get("phone_lookup_hash") != row.get("phone_lookup_hash"):
        client.table("accounts").update({
            "phone_number": protected["phone_number"],
            "phone_lookup_hash": protected["phone_lookup_hash"],
        }).eq("id", row["id"]).execute()
    return _decode_account_row(protected)


def _load_active_otp(client, phone: str) -> Optional[dict]:
    lookup = _phone_lookup_hash(phone)
    res = (client.table("otp_verifications").select("*")
           .eq("phone_lookup_hash", lookup).eq("is_used", False)
           .order("created_at", desc=True).limit(1).execute())
    if res.data:
        row = dict(res.data[0])
    else:
        legacy = (client.table("otp_verifications").select("*")
                  .eq("phone_number", phone).eq("is_used", False)
                  .order("created_at", desc=True).limit(1).execute())
        if not legacy.data:
            return None
        row = dict(legacy.data[0])
        protected = _encode_otp_row(row)
        client.table("otp_verifications").update({
            "phone_number": protected["phone_number"],
            "phone_lookup_hash": protected["phone_lookup_hash"],
        }).eq("id", row["id"]).execute()
    if (row.get("phone_number") or "").startswith("v2:"):
        from app.security.phi_protection import decrypt_phi_field
        row["phone_number"] = decrypt_phi_field(row["phone_number"])
    return row


_PROFILE_PHI_FIELDS = ("full_name", "age", "gender", "guardian_name")
_PROFILE_PHI_ARRAY_FIELDS = ("allergies", "medications", "known_conditions")


def _encode_profile_row(row: dict) -> dict:
    from app.security.phi_protection import encrypt_phi_field
    protected = dict(row)
    for field in _PROFILE_PHI_FIELDS:
        value = protected.get(field)
        if value and not str(value).startswith("v2:"):
            protected[field] = encrypt_phi_field(str(value))
    for field in _PROFILE_PHI_ARRAY_FIELDS:
        values = protected.get(field)
        if values:
            protected[field] = [encrypt_phi_field(str(v)) if v and not str(v).startswith("v2:") else v for v in values]
    return protected


def _decode_profile_row(row: Optional[dict]) -> Optional[dict]:
    if not row:
        return row
    from app.security.phi_protection import decrypt_phi_field
    profile = dict(row)
    for field in _PROFILE_PHI_FIELDS:
        value = profile.get(field)
        if isinstance(value, str) and value.startswith("v2:"):
            profile[field] = decrypt_phi_field(value)
    for field in _PROFILE_PHI_ARRAY_FIELDS:
        values = profile.get(field)
        if isinstance(values, list):
            profile[field] = [decrypt_phi_field(v) if isinstance(v, str) and v.startswith("v2:") else v for v in values]
    return profile


def _migrate_profile_row(client, row: dict) -> dict:
    protected = _encode_profile_row(row)
    changes = {k: protected[k] for k in (*_PROFILE_PHI_FIELDS, *_PROFILE_PHI_ARRAY_FIELDS) if protected.get(k) != row.get(k)}
    if changes:
        client.table("patient_profiles").update(changes).eq("id", row["id"]).execute()
    return _decode_profile_row(protected)


_CONSULTATION_PHI_TEXT_FIELDS = (
    "original_transcript", "english_transcript", "chief_complaint", "complaint_category", "audio_url",
)


def _encode_consultation_row(row: dict) -> dict:
    from app.security.phi_protection import encrypt_phi_field
    protected = dict(row)
    for field in _CONSULTATION_PHI_TEXT_FIELDS:
        value = protected.get(field)
        if value and not str(value).startswith("v2:"):
            protected[field] = encrypt_phi_field(str(value))
    result = protected.get("full_result")
    if result is not None and not (isinstance(result, dict) and "_phi_ciphertext_v2" in result):
        payload = json.dumps(result, ensure_ascii=False, separators=(",", ":"), default=str)
        protected["full_result"] = {"_phi_ciphertext_v2": encrypt_phi_field(payload)}
    prescriptions = protected.get("prescriptions")
    if prescriptions is not None and not (isinstance(prescriptions, dict) and "_phi_ciphertext_v2" in prescriptions):
        payload = json.dumps(prescriptions, ensure_ascii=False, separators=(",", ":"), default=str)
        protected["prescriptions"] = {"_phi_ciphertext_v2": encrypt_phi_field(payload)}
    return protected


def _decode_consultation_row(row: Optional[dict]) -> Optional[dict]:
    if not row:
        return row
    from app.security.phi_protection import decrypt_phi_field
    record = dict(row)
    for field in _CONSULTATION_PHI_TEXT_FIELDS:
        value = record.get(field)
        if isinstance(value, str) and value.startswith("v2:"):
            record[field] = decrypt_phi_field(value)
    result = record.get("full_result")
    if isinstance(result, dict) and result.get("_phi_ciphertext_v2"):
        record["full_result"] = json.loads(decrypt_phi_field(result["_phi_ciphertext_v2"]))
    prescriptions = record.get("prescriptions")
    if isinstance(prescriptions, dict) and prescriptions.get("_phi_ciphertext_v2"):
        record["prescriptions"] = json.loads(decrypt_phi_field(prescriptions["_phi_ciphertext_v2"]))
    return record


def _migrate_consultation_row(client, row: dict) -> dict:
    protected = _encode_consultation_row(row)
    changed = {k: protected.get(k) for k in (*_CONSULTATION_PHI_TEXT_FIELDS, "full_result", "prescriptions") if protected.get(k) != row.get(k)}
    if changed:
        client.table("consultations").update(changed).eq("id", row["id"]).execute()
    return _decode_consultation_row(protected)


def _encode_json_phi(value: Any) -> Any:
    if value is None or (isinstance(value, dict) and "_phi_ciphertext_v2" in value):
        return value
    from app.security.phi_protection import encrypt_phi_field
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    return {"_phi_ciphertext_v2": encrypt_phi_field(payload)}


def _decode_json_phi(value: Any) -> Any:
    if isinstance(value, dict) and value.get("_phi_ciphertext_v2"):
        from app.security.phi_protection import decrypt_phi_field
        return json.loads(decrypt_phi_field(value["_phi_ciphertext_v2"]))
    return value


def _encode_text_fields(row: dict, fields: Tuple[str, ...]) -> dict:
    from app.security.phi_protection import encrypt_phi_field
    encoded = dict(row)
    for field in fields:
        value = encoded.get(field)
        if value and not (isinstance(value, str) and value.startswith("v2:")):
            encoded[field] = encrypt_phi_field(str(value))
    return encoded


def _decode_text_fields(row: Optional[dict], fields: Tuple[str, ...]) -> Optional[dict]:
    if row is None:
        return None
    from app.security.phi_protection import decrypt_phi_field
    decoded = dict(row)
    for field in fields:
        value = decoded.get(field)
        if isinstance(value, str) and value.startswith("v2:"):
            decoded[field] = decrypt_phi_field(value)
    return decoded


def _encode_encrypted_json(value: Any) -> Optional[str]:
    if value is None:
        return None
    from app.security.phi_protection import encrypt_phi_field
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    return encrypt_phi_field(payload)


def _decode_encrypted_json(value: Optional[str]) -> Any:
    if not value:
        return None
    from app.security.phi_protection import decrypt_phi_field
    return json.loads(decrypt_phi_field(value))


# -----------------------------------------------------------------------------
# Password & OTP Cryptographic Hashing
# -----------------------------------------------------------------------------
def hash_password(password: str) -> str:
    """Hash password with PBKDF2-HMAC-SHA256."""
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100_000)
    return f"{salt.hex()}:{dk.hex()}"


def verify_password(password: str, hashed: str) -> bool:
    """Verify password against stored PBKDF2 hash."""
    try:
        parts = hashed.split(":")
        if len(parts) != 2:
            return False
        salt = bytes.fromhex(parts[0])
        expected_dk = bytes.fromhex(parts[1])
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100_000)
        return hmac.compare_digest(dk, expected_dk)
    except Exception:
        return False


def _hash_otp(otp_code: str, salt: Optional[str] = None) -> str:
    s = salt or uuid.uuid4().hex
    h = hashlib.sha256(f"{otp_code}:{s}".encode("utf-8")).hexdigest()
    return f"{s}${h}"


def _verify_otp_hash(otp_code: str, stored_hash: str) -> bool:
    if "$" in stored_hash:
        salt, h = stored_hash.split("$", 1)
        expected = hashlib.sha256(f"{otp_code}:{salt}".encode("utf-8")).hexdigest()
        return hmac.compare_digest(h, expected)
    legacy_salt = "grannus_otp_salt_2026"
    expected = hashlib.sha256(f"{otp_code}:{legacy_salt}".encode("utf-8")).hexdigest()
    return hmac.compare_digest(stored_hash, expected)


def _safe_uuid_or_none(val: Any) -> Optional[str]:
    if not val:
        return None
    val_str = str(val).strip()
    try:
        uuid.UUID(val_str)
        return val_str
    except (ValueError, AttributeError):
        return None


# -----------------------------------------------------------------------------
# Hospital & Department Operations (B3.2)
# -----------------------------------------------------------------------------
def get_hospitals() -> List[dict]:
    client = get_supabase_client()
    if client:
        try:
            res = client.table("hospitals").select("*").execute()
            if res.data:
                return res.data
        except Exception as exc:
            _handle_database_error("get_hospitals", exc)
    return _MEM_HOSPITALS


def get_default_hospital() -> dict:
    """Load the configured facility; production never falls back to demo data."""
    settings = get_settings()
    hospital_id = get_default_hospital_id()
    if hospital_id:
        hospital = get_hospital_by_id(hospital_id)
        if hospital:
            return hospital
    if settings.is_production:
        raise RuntimeError("Configured default hospital is unavailable in Supabase.")
    for hospital in _MEM_HOSPITALS:
        if not hospital_id or hospital.get("id") == hospital_id:
            return hospital
    raise RuntimeError("Configured default hospital is unavailable.")


def get_default_hospital_id() -> str:
    settings = get_settings()
    hospital_id = getattr(settings, "default_hospital_id", "")
    if hospital_id:
        return hospital_id
    if settings.is_production:
        raise RuntimeError("DEFAULT_HOSPITAL_ID is required in production.")
    return _MEM_HOSPITALS[0]["id"]


def get_hospital_by_id(hospital_id: str) -> Optional[dict]:
    client = get_supabase_client()
    if client:
        try:
            response = (client.table("hospitals").select("*")
                        .eq("id", hospital_id).limit(1).execute())
            return response.data[0] if response.data else None
        except Exception as exc:
            _handle_database_error("get_hospital_by_id", exc)
    if get_settings().is_development:
        return next((hospital for hospital in _MEM_HOSPITALS if hospital.get("id") == hospital_id), None)
    return None


def get_departments(hospital_id: Optional[str] = None) -> List[dict]:
    client = get_supabase_client()
    if client:
        try:
            query = client.table("departments").select("*")
            if hospital_id:
                query = query.eq("hospital_id", hospital_id)
            res = query.execute()
            if res.data:
                return res.data
        except Exception as exc:
            _handle_database_error("get_departments", exc)
    if hospital_id:
        return [d for d in _MEM_DEPARTMENTS if d["hospital_id"] == hospital_id]
    return _MEM_DEPARTMENTS


# -----------------------------------------------------------------------------
# Patient Account & PIN Sign-In (B2.2, B4.1)
# -----------------------------------------------------------------------------
# Demo / Development test numbers that bypass external SMS network
DEV_TEST_NUMBERS = {
    "+919876543210": "123456",
    "+919999999999": "654321",
    "9876543210": "123456",
}


def normalize_phone(phone: str) -> str:
    cleaned = phone.strip().replace(" ", "").replace("-", "")
    if not cleaned.startswith("+"):
        if cleaned.startswith("0") and len(cleaned) == 11:
            cleaned = f"+91{cleaned[1:]}"
        elif len(cleaned) == 10:
            cleaned = f"+91{cleaned}"
        elif cleaned.startswith("91") and len(cleaned) == 12:
            cleaned = f"+{cleaned}"
    return cleaned


def request_phone_otp(phone: str) -> Tuple[bool, str, Optional[str]]:
    """
    Generate 6-digit OTP for patient phone login.
    Enforces 30-second resend cooldown and 5-minute expiration.
    Fails closed if SMS delivery fails.
    Returns: (success, message, dev_otp_hint_if_dev)
    """
    return False, "SMS sign-in is disabled. Use patient PIN sign-in instead.", None
    from app.config import get_settings
    settings = get_settings()
    is_dev = settings.is_development or settings.allow_dev_otp

    phone_norm = normalize_phone(phone)
    now = time.time()
    client = get_supabase_client()

    # Use a database RPC for a cooldown shared by every production worker.
    if client:
        try:
            from app.security.phi_protection import phi_lookup_hash
            result = client.rpc("consume_otp_cooldown", {
                "p_key": phi_lookup_hash(phone_norm, "otp-cooldown"),
                "p_cooldown_seconds": 30,
            }).execute().data
            if isinstance(result, list) and result:
                result = result[0]
            if not isinstance(result, dict):
                raise RuntimeError("OTP cooldown RPC returned an invalid response")
            if not result.get("allowed"):
                remaining = max(1, int(result.get("retry_after", 1)))
                return False, f"Please wait {remaining} seconds before requesting a new code.", None
        except Exception as exc:
            if settings.is_production:
                logger.error("Distributed OTP cooldown unavailable: %s", exc)
                return False, "Verification service is temporarily unavailable. Please retry shortly.", None
            logger.warning("Using process-local OTP cooldown in non-production: %s", exc)
            client = None

    if not client:
        last_req = _OTP_COOLDOWNS.get(phone_norm, 0)
        if now - last_req < 30:
            remaining = int(30 - (now - last_req))
            return False, f"Please wait {remaining} seconds before requesting a new code.", None
        _OTP_COOLDOWNS[phone_norm] = now

    # Determine OTP code: strictly gate dev test numbers behind development
    if is_dev and (phone_norm in DEV_TEST_NUMBERS or phone in DEV_TEST_NUMBERS):
        otp_code = DEV_TEST_NUMBERS.get(phone_norm) or DEV_TEST_NUMBERS.get(phone)
    else:
        otp_code = f"{random.randint(100000, 999999):06d}"

    otp_hash = _hash_otp(otp_code)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    otp_id = str(uuid.uuid4())

    record = {
        "id": otp_id,
        "phone_number": phone_norm,
        "otp_hash": otp_hash,
        "attempts": 0,
        "max_attempts": 5,
        "expires_at": expires_at.isoformat(),
        "is_used": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    if client:
        try:
            client.table("otp_verifications").insert(_encode_otp_row(record)).execute()
        except Exception as exc:
            _handle_database_error("insert_otp", exc)
            if settings.is_production:
                return False, "Verification service is temporarily unavailable. Please retry shortly.", None

    # In-memory record
    if phone_norm not in _MEM_OTPS:
        _MEM_OTPS[phone_norm] = []
    _MEM_OTPS[phone_norm].append(record)

    # Dev hint only provided in development with dev test numbers
    dev_hint = otp_code if (is_dev and (phone_norm in DEV_TEST_NUMBERS or phone in DEV_TEST_NUMBERS)) else None

    # OTP delivery is disabled with the SMS provider. Patients use phone + PIN.
    return False, "SMS sign-in is disabled. Use patient PIN sign-in instead.", None


def verify_phone_otp(phone: str, otp_code: str) -> Tuple[bool, str, Optional[dict]]:
    """
    Disabled legacy verifier. Patient sign-in is handled through the PIN flow.
    """
    return False, "SMS sign-in is disabled. Use patient PIN sign-in instead.", None
    phone_norm = normalize_phone(phone)
    code_clean = otp_code.strip()
    now_iso = datetime.now(timezone.utc).isoformat()

    client = get_supabase_client()
    latest_record = None

    if client:
        try:
            latest_record = _load_active_otp(client, phone_norm)
        except Exception as exc:
            _handle_database_error("get_otp", exc)

    if not latest_record:
        user_otps = _MEM_OTPS.get(phone_norm, [])
        for r in reversed(user_otps):
            if not r["is_used"]:
                latest_record = r
                break

    if not latest_record:
        return False, "No active verification code found. Please request a new code.", None

    if latest_record["attempts"] >= latest_record.get("max_attempts", 5):
        return False, "Maximum verification attempts exceeded. Please request a new code.", None

    if latest_record["expires_at"] < now_iso:
        return False, "Verification code has expired. Please request a new code.", None

    # Verify hash with salt embedded in hash string
    expected_hash = latest_record["otp_hash"]
    if not _verify_otp_hash(code_clean, expected_hash):
        latest_record["attempts"] += 1
        for r in _MEM_OTPS.get(phone_norm, []):
            if r.get("id") == latest_record.get("id"):
                r["attempts"] = latest_record["attempts"]
        if client:
            try:
                client.table("otp_verifications").update({"attempts": latest_record["attempts"]}).eq("id", latest_record["id"]).execute()
            except Exception as exc:
                _handle_database_error("increment_otp_attempts", exc)
        remaining = latest_record["max_attempts"] - latest_record["attempts"]
        return False, f"Invalid verification code. {remaining} attempts remaining.", None

    # Success: Mark single-use as used across database and memory
    latest_record["is_used"] = True
    for r in _MEM_OTPS.get(phone_norm, []):
        r["is_used"] = True
    if client:
        try:
            client.table("otp_verifications").update({"is_used": True}).eq("id", latest_record["id"]).execute()
        except Exception as exc:
            _handle_database_error("consume_otp", exc)

    # Get or create account
    account = _get_or_create_account_db(phone_norm)
    return True, "Phone number verified successfully.", account


PIN_REGEX = re.compile(r"^\d{4}$")
DEV_TEST_PIN = "1234"


def _is_account_locked(account: dict) -> Tuple[bool, int]:
    """Check if account is temporarily locked due to failed PIN attempts."""
    locked_until_str = account.get("locked_until")
    if not locked_until_str:
        return False, 0
    try:
        locked_dt = datetime.fromisoformat(locked_until_str.replace("Z", "+00:00"))
        now_dt = datetime.now(timezone.utc)
        if locked_dt > now_dt:
            remaining = int((locked_dt - now_dt).total_seconds())
            return True, max(remaining, 1)
    except Exception:
        pass
    return False, 0


def check_patient_phone_status(phone: str) -> dict:
    """
    Check if a patient phone number exists in the database, whether a 4-digit PIN
    is set, and whether the account is currently locked.
    """
    from app.auth import hash_password
    phone_norm = normalize_phone(phone)
    client = get_supabase_client()
    acc = None

    if client:
        try:
            acc = _load_account_by_phone(client, phone_norm)
        except Exception as exc:
            _handle_database_error("check_phone_status", exc)

    if not acc:
        acc = _MEM_ACCOUNTS.get(phone_norm)

    # In dev/test environments, pre-seed recognized test numbers with default PIN "1234"
    from app.config import get_settings
    settings = get_settings()
    is_dev = settings.is_development or settings.allow_dev_otp
    if not acc and is_dev and (phone_norm in DEV_TEST_NUMBERS or phone in DEV_TEST_NUMBERS):
        acc = {
            "id": str(uuid.uuid4()),
            "phone_number": phone_norm,
            "pin_hash": hash_password(DEV_TEST_PIN),
            "failed_pin_attempts": 0,
            "locked_until": None,
            "is_verified": True,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        _MEM_ACCOUNTS[phone_norm] = acc

    if not acc:
        return {
            "phone_number": phone_norm,
            "exists": False,
            "has_pin": False,
            "is_locked": False,
            "remaining_lockout_seconds": 0,
        }

    is_locked, remaining_sec = _is_account_locked(acc)
    has_pin = bool(acc.get("pin_hash"))

    return {
        "phone_number": phone_norm,
        "exists": True,
        "has_pin": has_pin,
        "is_locked": is_locked,
        "remaining_lockout_seconds": remaining_sec,
    }


def register_patient_pin(phone: str, pin: str) -> Tuple[bool, str, Optional[dict]]:
    """
    Register compulsory 4-digit PIN for new patient or existing account without PIN.
    Persists salted PBKDF2 hash into database (accounts table) and memory.
    """
    from app.auth import hash_password
    clean_pin = pin.strip() if pin else ""
    if not PIN_REGEX.match(clean_pin):
        return False, "PIN must be exactly 4 numeric digits (0000-9999).", None

    phone_norm = normalize_phone(phone)
    client = get_supabase_client()
    acc = None

    if client:
        try:
            acc = _load_account_by_phone(client, phone_norm)
        except Exception as exc:
            _handle_database_error("lookup_pin_registration_account", exc)

    if not acc:
        acc = _MEM_ACCOUNTS.get(phone_norm)

    if acc and acc.get("pin_hash"):
        return False, "A security PIN is already configured for this phone number. Please sign in.", None

    pin_hash = hash_password(clean_pin)
    now_iso = datetime.now(timezone.utc).isoformat()

    if acc:
        acc["pin_hash"] = pin_hash
        acc["failed_pin_attempts"] = 0
        acc["locked_until"] = None
        acc["updated_at"] = now_iso
        if client:
            try:
                client.table("accounts").update({
                    "pin_hash": pin_hash,
                    "failed_pin_attempts": 0,
                    "locked_until": None,
                    "updated_at": now_iso,
                }).eq("id", acc["id"]).execute()
            except Exception as exc:
                _handle_database_error("update_patient_pin", exc)
        _MEM_ACCOUNTS[phone_norm] = acc
    else:
        new_acc = {
            "id": str(uuid.uuid4()),
            "phone_number": phone_norm,
            "pin_hash": pin_hash,
            "failed_pin_attempts": 0,
            "locked_until": None,
            "is_verified": True,
            "created_at": now_iso,
            "updated_at": now_iso,
        }
        if client:
            try:
                ins = client.table("accounts").insert(_encode_account_row(new_acc)).execute()
                if ins.data:
                    new_acc = _decode_account_row(ins.data[0])
            except Exception as exc:
                _handle_database_error("insert_account_with_pin", exc)
        _MEM_ACCOUNTS[phone_norm] = new_acc
        acc = new_acc

    # Ensure default Self profile exists for account
    profiles = get_profiles_for_account(acc["id"])
    if not profiles:
        create_patient_profile(acc["id"], {
            "full_name": "Self",
            "relation": "self",
            "preferred_language": "ta-IN",
        })

    logger.info("4-digit PIN registered successfully for patient phone=%s", phone_norm[:5] + "****" + phone_norm[-2:])
    return True, "Security PIN created successfully.", acc


def verify_patient_pin(phone: str, pin: str) -> Tuple[bool, str, Optional[dict]]:
    """
    Verify 4-digit PIN for patient authentication against database PBKDF2 hash.
    Enforces maximum 5 attempts with 15-minute lockout.
    """
    from app.auth import verify_password, hash_password
    clean_pin = pin.strip() if pin else ""
    if not PIN_REGEX.match(clean_pin):
        return False, "PIN must be exactly 4 numeric digits.", None

    phone_norm = normalize_phone(phone)
    client = get_supabase_client()
    acc = None

    if client:
        try:
            acc = _load_account_by_phone(client, phone_norm)
        except Exception as exc:
            _handle_database_error("lookup_pin_verification_account", exc)

    if not acc:
        acc = _MEM_ACCOUNTS.get(phone_norm)

    # In dev/test, automatically seed dev test numbers with default PIN "1234" if not present
    from app.config import get_settings
    settings = get_settings()
    is_dev = settings.is_development or settings.allow_dev_otp
    if not acc and is_dev and (phone_norm in DEV_TEST_NUMBERS or phone in DEV_TEST_NUMBERS):
        acc = {
            "id": str(uuid.uuid4()),
            "phone_number": phone_norm,
            "pin_hash": hash_password(DEV_TEST_PIN),
            "failed_pin_attempts": 0,
            "locked_until": None,
            "is_verified": True,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        _MEM_ACCOUNTS[phone_norm] = acc

    if not acc:
        return False, "No account found for this phone number. Please set up your PIN.", None

    if not acc.get("pin_hash"):
        return False, "No security PIN set for this account. Please create your PIN.", None

    # Check lockout
    is_locked, remaining_sec = _is_account_locked(acc)
    if is_locked:
        mins = max(1, (remaining_sec + 59) // 60)
        return False, f"Account temporarily locked due to too many failed attempts. Try again in {mins} minute(s).", None

    stored_hash = acc["pin_hash"]
    if not verify_password(clean_pin, stored_hash):
        current_fails = acc.get("failed_pin_attempts", 0) + 1
        acc["failed_pin_attempts"] = current_fails
        update_dict: dict = {"failed_pin_attempts": current_fails}

        if current_fails >= 5:
            lockout_time = datetime.now(timezone.utc) + timedelta(minutes=15)
            acc["locked_until"] = lockout_time.isoformat()
            update_dict["locked_until"] = lockout_time.isoformat()
            if client:
                try:
                    client.table("accounts").update(update_dict).eq("id", acc["id"]).execute()
                except Exception as exc:
                    _handle_database_error("lock_patient_pin_account", exc)
            _MEM_ACCOUNTS[phone_norm] = acc
            return False, "Maximum failed attempts exceeded. Account is locked for 15 minutes.", None

        if client:
            try:
                client.table("accounts").update(update_dict).eq("id", acc["id"]).execute()
            except Exception as exc:
                _handle_database_error("increment_patient_pin_attempts", exc)
        _MEM_ACCOUNTS[phone_norm] = acc
        attempts_left = 5 - current_fails
        return False, f"Incorrect 4-digit PIN. {attempts_left} attempt(s) remaining.", None

    # Successful PIN match: reset failed attempts
    acc["failed_pin_attempts"] = 0
    acc["locked_until"] = None
    if client:
        try:
            client.table("accounts").update({
                "failed_pin_attempts": 0,
                "locked_until": None,
            }).eq("id", acc["id"]).execute()
        except Exception as exc:
            _handle_database_error("reset_patient_pin_attempts", exc)
    _MEM_ACCOUNTS[phone_norm] = acc

    # Ensure profile exists
    profiles = get_profiles_for_account(acc["id"])
    if not profiles:
        create_patient_profile(acc["id"], {
            "full_name": "Self",
            "relation": "self",
            "preferred_language": "ta-IN",
        })

    logger.info("Patient authenticated successfully via 4-digit PIN: phone=%s", phone_norm[:5] + "****" + phone_norm[-2:])
    return True, "Authenticated successfully.", acc


def _get_or_create_account_db(phone_norm: str) -> dict:
    client = get_supabase_client()
    if client:
        try:
            acc = _load_account_by_phone(client, phone_norm)
            if acc:
                return acc
            new_acc = {
                "id": str(uuid.uuid4()),
                "phone_number": phone_norm,
                "pin_hash": None,
                "failed_pin_attempts": 0,
                "locked_until": None,
                "is_verified": True,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            ins = client.table("accounts").insert(_encode_account_row(new_acc)).execute()
            if ins.data:
                return _decode_account_row(ins.data[0])
        except Exception as exc:
            _handle_database_error("get_or_create_account", exc)

    if phone_norm in _MEM_ACCOUNTS:
        return _MEM_ACCOUNTS[phone_norm]

    new_acc = {
        "id": str(uuid.uuid4()),
        "phone_number": phone_norm,
        "pin_hash": None,
        "failed_pin_attempts": 0,
        "locked_until": None,
        "is_verified": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _MEM_ACCOUNTS[phone_norm] = new_acc
    return new_acc


def get_patient_account_by_id(account_id: str) -> Optional[dict]:
    """Retrieve patient account by ID from Supabase or in-memory store."""
    client = get_supabase_client()
    if client and _safe_uuid_or_none(account_id):
        try:
            res = (client.table("accounts").select("*")
                   .eq("id", account_id).eq("is_active", True).execute())
            if res.data:
                return _migrate_account_row(client, res.data[0])
        except Exception as exc:
            _handle_database_error("get_patient_account_by_id", exc)

    for acc in _MEM_ACCOUNTS.values():
        if acc.get("id") == account_id:
            return acc
    return None


# -----------------------------------------------------------------------------
# Patient Profiles (B4.1 - Multi-profile per phone account)
# -----------------------------------------------------------------------------
def get_profiles_for_account(account_id: str) -> List[dict]:
    client = get_supabase_client()
    if client:
        try:
            res = client.table("patient_profiles").select("*").eq("account_id", account_id).execute()
            if res.data:
                return [_migrate_profile_row(client, p) for p in res.data]
        except Exception as exc:
            _handle_database_error("get_profiles", exc)
    return [p for p in _MEM_PROFILES.values() if p.get("account_id") == account_id]


def create_patient_profile(account_id: str, profile_data: dict) -> dict:
    profile_id = profile_data.get("id") or str(uuid.uuid4())
    record = {
        "id": profile_id,
        "account_id": account_id,
        "hospital_id": profile_data.get("hospital_id"),
        "full_name": profile_data.get("full_name", "Family Member"),
        "age": str(profile_data.get("age", "")),
        "gender": profile_data.get("gender", ""),
        "relation": profile_data.get("relation", "self"),
        "preferred_language": profile_data.get("preferred_language", "ta-IN"),
        "allergies": profile_data.get("allergies", []),
        "medications": profile_data.get("medications", []),
        "known_conditions": profile_data.get("known_conditions", []),
        "pin_hash": profile_data.get("pin_hash"),
        "guardian_name": profile_data.get("guardian_name"),
        "is_caregiver_managed": bool(profile_data.get("is_caregiver_managed", False)),
        "caregiver_relationship": profile_data.get("caregiver_relationship"),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    client = get_supabase_client()
    if client:
        try:
            res = client.table("patient_profiles").insert(_encode_profile_row(record)).execute()
            if res.data:
                return _decode_profile_row(res.data[0])
        except Exception as exc:
            _handle_database_error("create_profile", exc)

    _MEM_PROFILES[profile_id] = record
    return record


def get_patient_profile(profile_id: str) -> Optional[dict]:
    client = get_supabase_client()
    if client:
        try:
            res = client.table("patient_profiles").select("*").eq("id", profile_id).execute()
            if res.data:
                return _migrate_profile_row(client, res.data[0])
        except Exception as exc:
            _handle_database_error("get_profile", exc)
    return _MEM_PROFILES.get(profile_id)


# -----------------------------------------------------------------------------
# Staff Authentication (B2.3 - Doctors, Nurses, Admins)
# -----------------------------------------------------------------------------
# Seed fallback staff credentials (8 doctors B5.1 + admin + nurse)
_FALLBACK_STAFF = {
    "dr.rajan@hospital.in": {
        "id": "11111111-0001-0000-0000-000000000001",
        "email": "dr.rajan@hospital.in",
        "password_hash": hash_password("grannus_secure_doctor_2026"),
        "role": "doctor",
        "full_name": "Dr. Rajan K., MD",
        "qualifications": "MBBS, MD (General Medicine)",
        "doctor_registration_number": "TNMC-48291",
        "state_council": "Tamil Nadu Medical Council",
        "specialty": "General Medicine",
        "department_code": "GEN_MED",
        "opd_room": "Room 101, Ground Floor OPD Block",
        "experience": "16 Years Clinical Practice",
        "languages": ["ta", "en", "hi"],
        "is_active": True,
        "is_on_duty": True,
        "active_case_load": 0,
        "max_capacity": 20,
        "weekly_schedule": {"mon_fri": "09:00-17:00", "sat": "09:00-13:00"},
        "is_verified_doctor": True,
    },
    "dr.priya@hospital.in": {
        "id": "11111111-0002-0000-0000-000000000002",
        "email": "dr.priya@hospital.in",
        "password_hash": hash_password("grannus_priya_dm_2026!"),
        "role": "doctor",
        "full_name": "Dr. Priya Sundaram, DM",
        "qualifications": "MBBS, MD, DM (Cardiology), FACC",
        "doctor_registration_number": "TNMC-54321",
        "state_council": "Tamil Nadu Medical Council",
        "specialty": "Cardiology",
        "department_code": "CARDIOLOGY",
        "opd_room": "Room 201, 2nd Floor Cardiology Wing",
        "experience": "13 Years Clinical Practice",
        "languages": ["ta", "en"],
        "is_active": True,
        "is_on_duty": True,
        "active_case_load": 0,
        "max_capacity": 10,
        "weekly_schedule": {"mon_fri": "08:00-16:00"},
        "is_verified_doctor": True,
    },
    "dr.vikram@hospital.in": {
        "id": "11111111-0003-0000-0000-000000000003",
        "email": "dr.vikram@hospital.in",
        "password_hash": hash_password("grannus_vikram_peds_2026!"),
        "role": "doctor",
        "full_name": "Dr. Vikram Patel, MD",
        "qualifications": "MBBS, MD (Pediatrics), DNB",
        "doctor_registration_number": "GMC-39104",
        "state_council": "Gujarat Medical Council",
        "specialty": "Pediatrics",
        "department_code": "PEDIATRICS",
        "opd_room": "Room 105, Child Health & Immunization Clinic",
        "experience": "11 Years Clinical Practice",
        "languages": ["hi", "en", "te"],
        "is_active": True,
        "is_on_duty": True,
        "active_case_load": 0,
        "max_capacity": 15,
        "weekly_schedule": {"mon_sat": "10:00-18:00"},
        "is_verified_doctor": True,
    },
    "dr.shalini@hospital.in": {
        "id": "11111111-0004-0000-0000-000000000004",
        "email": "dr.shalini@hospital.in",
        "password_hash": hash_password("grannus_shalini_derm_2026!"),
        "role": "doctor",
        "full_name": "Dr. Shalini Reddy, MD",
        "qualifications": "MBBS, MD (DVL - Dermatology)",
        "doctor_registration_number": "APMC-28491",
        "state_council": "Andhra Pradesh Medical Council",
        "specialty": "Dermatology",
        "department_code": "DERMATOLOGY",
        "opd_room": "Room 115, Cutaneous & Skin Care Clinic",
        "experience": "10 Years Clinical Practice",
        "languages": ["te", "en"],
        "is_active": True,
        "is_on_duty": True,
        "active_case_load": 0,
        "max_capacity": 15,
        "weekly_schedule": {"tue_sat": "09:00-17:00"},
        "is_verified_doctor": True,
    },
    "dr.meenakshi@hospital.in": {
        "id": "11111111-0005-0000-0000-000000000005",
        "email": "dr.meenakshi@hospital.in",
        "password_hash": hash_password("grannus_meenakshi_obgyn_2026!"),
        "role": "doctor",
        "full_name": "Dr. Meenakshi Iyer, MS",
        "qualifications": "MBBS, MS (Obstetrics & Gynecology), DGO",
        "doctor_registration_number": "TNMC-61245",
        "state_council": "Tamil Nadu Medical Council",
        "specialty": "Obstetrics & Gynecology",
        "department_code": "OBGYN",
        "opd_room": "Room 108, Maternal & Antenatal Care Block",
        "experience": "15 Years Clinical Practice",
        "languages": ["ta", "en"],
        "is_active": True,
        "is_on_duty": True,
        "active_case_load": 0,
        "max_capacity": 12,
        "weekly_schedule": {"mon_fri": "09:00-17:00"},
        "is_verified_doctor": True,
    },
    "dr.arvind@hospital.in": {
        "id": "11111111-0006-0000-0000-000000000006",
        "email": "dr.arvind@hospital.in",
        "password_hash": hash_password("grannus_arvind_ortho_2026!"),
        "role": "doctor",
        "full_name": "Dr. Arvind Kumar, MS",
        "qualifications": "MBBS, MS (Orthopaedics), MCh",
        "doctor_registration_number": "DMC-50123",
        "state_council": "Delhi Medical Council",
        "specialty": "Orthopaedics",
        "department_code": "ORTHOPEDICS",
        "opd_room": "Room 112, Orthopaedic Trauma & Fracture Clinic",
        "experience": "12 Years Clinical Practice",
        "languages": ["hi", "ta", "en"],
        "is_active": True,
        "is_on_duty": True,
        "active_case_load": 0,
        "max_capacity": 15,
        "weekly_schedule": {"mon_fri": "10:00-18:00"},
        "is_verified_doctor": True,
    },
    "dr.suresh@hospital.in": {
        "id": "11111111-0007-0000-0000-000000000007",
        "email": "dr.suresh@hospital.in",
        "password_hash": hash_password("grannus_suresh_ent_2026!"),
        "role": "doctor",
        "full_name": "Dr. Suresh Varma, MS",
        "qualifications": "MBBS, MS (ENT - Otorhinolaryngology)",
        "doctor_registration_number": "TSMC-33109",
        "state_council": "Telangana State Medical Council",
        "specialty": "ENT",
        "department_code": "ENT",
        "opd_room": "Room 118, ENT & Audiology Suite",
        "experience": "14 Years Clinical Practice",
        "languages": ["te", "ta", "en"],
        "is_active": True,
        "is_on_duty": True,
        "active_case_load": 0,
        "max_capacity": 15,
        "weekly_schedule": {"mon_fri": "09:00-17:00"},
        "is_verified_doctor": True,
    },
    "dr.ananya@hospital.in": {
        "id": "11111111-0009-0000-0000-000000000009",
        "email": "dr.ananya@hospital.in",
        "password_hash": hash_password("grannus_ananya_pulm_2026!"),
        "role": "doctor",
        "full_name": "Dr. Ananya Mukherjee, MD, DM",
        "qualifications": "MBBS, MD (Respiratory Medicine), DM",
        "doctor_registration_number": "WBMC-58210",
        "state_council": "West Bengal Medical Council",
        "specialty": "Pulmonology",
        "department_code": "PULMONOLOGY",
        "opd_room": "Room 204, Chest Clinic & Pulmonary Lab",
        "experience": "12 Years Clinical Practice",
        "languages": ["hi", "en", "ta"],
        "is_active": True,
        "is_on_duty": True,
        "active_case_load": 0,
        "max_capacity": 15,
        "weekly_schedule": {"mon_fri": "09:00-17:00"},
        "is_verified_doctor": True,
    },
    "dr.karthik@hospital.in": {
        "id": "11111111-0010-0000-0000-000000000010",
        "email": "dr.karthik@hospital.in",
        "password_hash": hash_password("grannus_karthik_neuro_2026!"),
        "role": "doctor",
        "full_name": "Dr. Karthik Narayanan, MD, DM",
        "qualifications": "MBBS, MD, DM (Neurology)",
        "doctor_registration_number": "TNMC-79401",
        "state_council": "Tamil Nadu Medical Council",
        "specialty": "Neurology",
        "department_code": "NEUROLOGY",
        "opd_room": "Room 208, Stroke & Neurological Care Unit",
        "experience": "15 Years Clinical Practice",
        "languages": ["ta", "en", "te"],
        "is_active": True,
        "is_on_duty": True,
        "active_case_load": 0,
        "max_capacity": 12,
        "weekly_schedule": {"mon_fri": "09:00-17:00"},
        "is_verified_doctor": True,
    },
    "dr.deepa@hospital.in": {
        "id": "11111111-0011-0000-0000-000000000011",
        "email": "dr.deepa@hospital.in",
        "password_hash": hash_password("grannus_deepa_gastro_2026!"),
        "role": "doctor",
        "full_name": "Dr. Deepa Shenoy, MD, DM",
        "qualifications": "MBBS, MD, DM (Medical Gastroenterology)",
        "doctor_registration_number": "KMC-46190",
        "state_council": "Karnataka Medical Council",
        "specialty": "Gastroenterology",
        "department_code": "GASTROENTEROLOGY",
        "opd_room": "Room 210, Digestive Diseases OPD",
        "experience": "13 Years Clinical Practice",
        "languages": ["en", "hi", "ta"],
        "is_active": True,
        "is_on_duty": True,
        "active_case_load": 0,
        "max_capacity": 15,
        "weekly_schedule": {"mon_fri": "09:30-17:30"},
        "is_verified_doctor": True,
    },
    "dr.senthil@hospital.in": {
        "id": "11111111-0012-0000-0000-000000000012",
        "email": "dr.senthil@hospital.in",
        "password_hash": hash_password("grannus_senthil_eye_2026!"),
        "role": "doctor",
        "full_name": "Dr. Senthil Nathan, MS, DO",
        "qualifications": "MBBS, MS (Ophthalmology), DO",
        "doctor_registration_number": "TNMC-65912",
        "state_council": "Tamil Nadu Medical Council",
        "specialty": "Ophthalmology",
        "department_code": "OPHTHALMOLOGY",
        "opd_room": "Room 120, Eye Care OPD & Refraction Unit",
        "experience": "17 Years Clinical Practice",
        "languages": ["ta", "en"],
        "is_active": True,
        "is_on_duty": True,
        "active_case_load": 0,
        "max_capacity": 20,
        "weekly_schedule": {"mon_sat": "08:30-15:30"},
        "is_verified_doctor": True,
    },
    "dr.harish@hospital.in": {
        "id": "11111111-0008-0000-0000-000000000008",
        "email": "dr.harish@hospital.in",
        "password_hash": hash_password("grannus_harish_er_2026!"),
        "role": "doctor",
        "full_name": "Dr. Harish Naidu, MBBS, MEM",
        "qualifications": "MBBS, MEM (Emergency Medicine), ACLS Certified",
        "doctor_registration_number": "TNMC-72019",
        "state_council": "Tamil Nadu Medical Council",
        "specialty": "Emergency & Acute Care",
        "department_code": "EMERGENCY",
        "opd_room": "Resuscitation Bay 1, Casualty Emergency Block",
        "experience": "9 Years Emergency & Trauma Practice",
        "languages": ["ta", "hi", "te", "en"],
        "is_active": True,
        "is_on_duty": True,
        "active_case_load": 0,
        "max_capacity": 25,
        "weekly_schedule": {"all_week": "24_hours"},
        "is_verified_doctor": True,
    },
    "dr_clinician": {
        "id": "doc_clinician_002",
        "email": "dr.clinician@hospital.in",
        "password_hash": hash_password("grannus_clinician_lab_2026!"),
        "role": "doctor",
        "full_name": "Dr. Clinician Lab Specialist",
        "doctor_registration_number": "TNMC-48291",
        "state_council": "Tamil Nadu Medical Council",
        "specialty": "General Medicine",
        "department_code": "GEN_MED",
        "languages": ["ta", "en", "hi"],
        "is_active": True,
        "is_on_duty": True,
        "active_case_load": 0,
        "max_capacity": 20,
        "weekly_schedule": {"mon_fri": "09:00-17:00"},
        "is_verified_doctor": True,
    },
    "admin_user": {
        "id": "admin_sys_002",
        "email": "admin_user@hospital.in",
        "password_hash": hash_password("grannus_admin_user_2026!"),
        "role": "admin",
        "full_name": "System Administrator",
        "is_active": True,
        "is_on_duty": True,
    },
    "admin@hospital.in": {
        "id": "admin_sys_001",
        "email": "admin@hospital.in",
        "password_hash": hash_password("grannus_admin_2026"),
        "role": "admin",
        "full_name": "Medical Superintendent",
        "is_active": True,
        "is_on_duty": True,
    },
    "nurse.mary@hospital.in": {
        "id": "nurse_mary_001",
        "email": "nurse.mary@hospital.in",
        "password_hash": hash_password("grannus_nurse_2026"),
        "role": "nurse",
        "full_name": "Staff Nurse Deepa R.",
        "is_active": True,
        "is_on_duty": True,
    },
}


def find_staff_user(email: str) -> Optional[dict]:
    """Find staff user record by email or identifier."""
    email_clean = email.strip().lower()
    client = get_supabase_client()
    if client:
        try:
            res = client.table("staff_users").select("*").eq("email", email_clean).execute()
            if res.data:
                return res.data[0]
            res = client.table("staff_users").select("*").eq("id", email.strip()).limit(1).execute()
            if res.data:
                return res.data[0]
        except Exception as exc:
            _handle_database_error("find_staff_user", exc)

    # In production, fallback in-memory registry is strictly forbidden (A2)
    from app.config import get_settings
    settings = get_settings()
    if settings.is_development:
        for user_key, user in _FALLBACK_STAFF.items():
            if user["email"].lower() == email_clean or user_key.lower() == email_clean or user.get("id") == email.strip():
                return user
    return None


def create_staff_user(
    email: str,
    password: str,
    role: str = "doctor",
    full_name: str = "",
    doctor_registration_number: Optional[str] = None,
    state_council: Optional[str] = None,
    specialty: Optional[str] = None,
    department_code: Optional[str] = "GEN_MED",
    languages: Optional[List[str]] = None,
    hospital_id: Optional[str] = None,
) -> dict:
    """
    Create a new staff user with a unique, admin-created PBKDF2 salt-hashed password (A2).
    Stored in database staff_users table, with fallback to in-memory store in development.
    """
    email_clean = email.strip().lower()
    pw_hash = hash_password(password)
    user_id = str(uuid.uuid4())
    record = {
        "id": user_id,
        "email": email_clean,
        "password_hash": pw_hash,
        "role": role,
        "full_name": full_name or email_clean,
        "doctor_registration_number": doctor_registration_number,
        "state_council": state_council,
        "specialty": specialty or "General Medicine",
        "department_code": department_code or "GEN_MED",
        "languages": languages or ["ta", "en", "hi"],
        "hospital_id": hospital_id or get_default_hospital_id(),
        "is_active": True,
        "is_on_duty": True,
        # Registration IDs are inputs for human verification, not proof of licensure.
        "is_verified_doctor": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    client = get_supabase_client()
    if client:
        try:
            client.table("staff_users").upsert(record).execute()
        except Exception as exc:
            _handle_database_error("insert_staff_user", exc)

    _FALLBACK_STAFF[email_clean] = record
    return record


_STAFF_FAILED_ATTEMPTS: Dict[str, Tuple[int, float]] = {}


def _get_staff_lockout(email_clean: str) -> Tuple[int, float]:
    """Retrieve failed attempts and lockout expiration from DB or fast in-memory cache (C1)."""
    client = get_supabase_client()
    if client:
        try:
            res = client.table("staff_lockouts").select("failed_count, lockout_until").eq("email", email_clean).execute()
            if res.data:
                row = res.data[0]
                lockout_until = row.get("lockout_until")
                lockout_dt = datetime.fromisoformat(lockout_until).timestamp() if lockout_until else 0.0
                return int(row.get("failed_count", 0)), lockout_dt
        except Exception as exc:
            _handle_database_error("get_staff_lockout", exc)
    return _STAFF_FAILED_ATTEMPTS.get(email_clean, (0, 0.0))


def _record_staff_failed_attempt(email_clean: str, count: int, lockout_until: float):
    """Persist failed login attempt and lockout to DB and in-memory cache (C1)."""
    _STAFF_FAILED_ATTEMPTS[email_clean] = (count, lockout_until)
    client = get_supabase_client()
    if client:
        try:
            lockout_iso = datetime.fromtimestamp(lockout_until, timezone.utc).isoformat() if lockout_until > 0 else None
            client.table("staff_lockouts").upsert({
                "email": email_clean,
                "failed_count": count,
                "lockout_until": lockout_iso,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }).execute()
        except Exception as exc:
            _handle_database_error("record_staff_failed_attempt", exc)


def _clear_staff_lockout(email_clean: str):
    """Clear failed login attempts upon successful login (C1)."""
    _STAFF_FAILED_ATTEMPTS.pop(email_clean, None)
    client = get_supabase_client()
    if client:
        try:
            client.table("staff_lockouts").delete().eq("email", email_clean).execute()
        except Exception as exc:
            _handle_database_error("clear_staff_lockout", exc)


def authenticate_staff_user(email: str, password: str) -> Optional[dict]:
    """
    Verify staff login with email and password.
    Enforces lockout policy: 5 consecutive failed attempts locks account for 15 minutes.
    In production, fallback in-memory registry is strictly forbidden (A2).
    """
    email_clean = email.strip().lower()
    now = time.time()

    # Database-backed lockout check (C1)
    failed_count, lockout_until = _get_staff_lockout(email_clean)
    if failed_count >= 5 and now < lockout_until:
        logger.warning(
            "Staff login blocked: %s is locked until %s",
            email_clean,
            datetime.fromtimestamp(lockout_until, timezone.utc).isoformat(),
        )
        return None

    user = None
    client = get_supabase_client()
    if client:
        try:
            res = client.table("staff_users").select("*").eq("email", email_clean).eq("is_active", True).execute()
            if res.data:
                db_user = res.data[0]
                if verify_password(password, db_user["password_hash"]):
                    user = db_user
        except Exception as exc:
            _handle_database_error("authenticate_staff", exc)

    # In-memory fallback is strictly gated to development and test environments (A2).
    # Production environments MUST have staff accounts registered in database staff_users table.
    from app.config import get_settings
    settings = get_settings()

    if not user:
        if not settings.is_development:
            logger.warning(
                "Production staff authentication refused fallback registry for %s. "
                "Staff must be provisioned in database staff_users table via admin script.",
                email_clean,
            )
        else:
            for user_key, u in _FALLBACK_STAFF.items():
                if u["email"].lower() == email_clean or user_key.lower() == email_clean:
                    if verify_password(password, u["password_hash"]):
                        user = u
                        break

    if user:
        _clear_staff_lockout(email_clean)
        return user

    new_count = failed_count + 1
    new_lockout = (now + 900.0) if new_count >= 5 else 0.0
    _record_staff_failed_attempt(email_clean, new_count, new_lockout)
    logger.warning("Failed staff auth for %s (attempt %d/5)", email_clean, new_count)
    return None


# -----------------------------------------------------------------------------
# Emergency SOS Dispatch Management (E2)
# -----------------------------------------------------------------------------
_MEM_SOS_DISPATCHES: List[dict] = []


def save_sos_dispatch(data: dict) -> dict:
    """Save an emergency SOS dispatch record to database or in-memory fallback."""
    dispatch_id = str(uuid.uuid4())
    record = {
        "id": dispatch_id,
        "patient_name": data.get("patient_name") or "Emergency Patient",
        "patient_phone": data.get("patient_phone"),
        "contact_phone": data.get("contact_phone"),
        "latitude": data.get("latitude"),
        "longitude": data.get("longitude"),
        "accuracy": data.get("accuracy"),
        "emergency_type": data.get("emergency_type") or "SEVERE_MEDICAL_DISTRESS",
        "status": "RECORDED",
        "notes": data.get("notes"),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    client = get_supabase_client()
    if client:
        try:
            db_record = _encode_text_fields(
                record, ("patient_name", "patient_phone", "contact_phone", "notes", "emergency_type")
            )
            db_record["location_ciphertext"] = _encode_encrypted_json({
                "latitude": record.get("latitude"),
                "longitude": record.get("longitude"),
                "accuracy": record.get("accuracy"),
            })
            db_record["latitude"] = None
            db_record["longitude"] = None
            db_record["accuracy"] = None
            client.table("emergency_sos_dispatches").insert(db_record).execute()
        except Exception as exc:
            _handle_database_error("save_sos_dispatch", exc)
    _MEM_SOS_DISPATCHES.append(record)
    return record


def get_sos_dispatches() -> List[dict]:
    client = get_supabase_client()
    if client:
        try:
            res = client.table("emergency_sos_dispatches").select("*").order("created_at", desc=True).execute()
            if res.data:
                rows = []
                for row in res.data:
                    decoded = _decode_text_fields(
                        row, ("patient_name", "patient_phone", "contact_phone", "notes", "emergency_type")
                    )
                    location = _decode_encrypted_json(decoded.get("location_ciphertext"))
                    if location:
                        decoded.update(location)
                    decoded.pop("location_ciphertext", None)
                    rows.append(decoded)
                return rows
        except Exception as exc:
            logger.error("Failed to fetch SOS dispatches from Supabase: %s", exc)
    return list(reversed(_MEM_SOS_DISPATCHES))


# -----------------------------------------------------------------------------
# Persistent Pipeline Jobs (B3.1)
# -----------------------------------------------------------------------------
def save_pipeline_job(job_id: str, job_data: dict) -> dict:
    record = {
        "id": job_id,
        "hospital_id": job_data.get("hospital_id"),
        "account_id": job_data.get("account_id"),
        "profile_id": job_data.get("profile_id"),
        "guest_case_id": job_data.get("guest_case_id"),
        "status": job_data.get("status", "QUEUED"),
        "stage": job_data.get("stage", "QUEUED"),
        "progress": job_data.get("progress", 0),
        "message": job_data.get("message", ""),
        "result": job_data.get("result"),
        "error_message": job_data.get("error_message"),
        "encrypted_audio_payload": job_data.get("encrypted_audio_payload"),
        "audio_filename": job_data.get("audio_filename"),
        "language_code": job_data.get("language_code"),
        "patient_context": job_data.get("patient_context"),
        "doctor_preferred_language": job_data.get("doctor_preferred_language"),
        "worker_id": job_data.get("worker_id"),
        "created_at": (
            datetime.fromtimestamp(job_data["created_at"], timezone.utc).isoformat()
            if isinstance(job_data.get("created_at"), (int, float))
            else job_data.get("created_at")
        ),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    client = get_supabase_client()
    if client:
        try:
            db_record = dict(record)
            db_record["result"] = _encode_json_phi(record["result"])
            db_record["patient_context"] = _encode_json_phi(record["patient_context"])
            db_record = _encode_text_fields(db_record, ("message", "error_message"))
            client.table("pipeline_jobs").upsert(db_record).execute()
        except Exception as exc:
            # If the remote database has not yet applied the durable queue migration,
            # retry with only known columns in progressive fallbacks so core job progress is still preserved.
            if "column" in str(exc).lower() or "pgrst204" in str(exc).lower():
                try:
                    # Level 1 fallback: exclude unapplied durable audio columns but keep guest_case_id if available
                    fallback_record = {
                        k: db_record[k]
                        for k in (
                            "id", "hospital_id", "account_id", "profile_id", "guest_case_id",
                            "status", "stage", "progress", "message", "result", "error_message",
                            "created_at", "updated_at",
                        )
                        if k in db_record and db_record[k] is not None
                    }
                    client.table("pipeline_jobs").upsert(fallback_record).execute()
                except Exception as inner_exc:
                    # Level 2 fallback: base pipeline_jobs table without guest_case_id
                    if "column" in str(inner_exc).lower() or "pgrst204" in str(inner_exc).lower():
                        try:
                            base_record = {
                                k: fallback_record[k]
                                for k in (
                                    "id", "hospital_id", "account_id", "profile_id",
                                    "status", "stage", "progress", "message", "result", "error_message",
                                    "created_at", "updated_at",
                                )
                                if k in fallback_record
                            }
                            client.table("pipeline_jobs").upsert(base_record).execute()
                        except Exception as base_exc:
                            _handle_database_error("save_pipeline_job", base_exc)
                    else:
                        _handle_database_error("save_pipeline_job", inner_exc)
            else:
                _handle_database_error("save_pipeline_job", exc)

    _MEM_JOBS[job_id] = record
    return record


def get_pipeline_job(job_id: str) -> Optional[dict]:
    client = get_supabase_client()
    if client:
        try:
            res = client.table("pipeline_jobs").select("*").eq("id", job_id).execute()
            if res.data:
                row = dict(res.data[0])
                row["result"] = _decode_json_phi(row.get("result"))
                row["patient_context"] = _decode_json_phi(row.get("patient_context"))
                row = _decode_text_fields(row, ("message", "error_message"))
                return row
        except Exception as exc:
            _handle_database_error("get_pipeline_job", exc)
    return _MEM_JOBS.get(job_id)


def list_recoverable_pipeline_jobs() -> List[dict]:
    """Find in-flight or queued pipeline jobs that can be recovered across workers."""
    client = get_supabase_client()
    if client:
        try:
            res = (
                client.table("pipeline_jobs")
                .select("*")
                .in_("status", ["QUEUED", "PREPROCESSING", "TRANSCRIBING", "EXTRACTING", "TRIAGING"])
                .order("created_at", desc=False)
                .limit(20)
                .execute()
            )
            jobs = []
            for item in res.data or []:
                row = dict(item)
                row["result"] = _decode_json_phi(row.get("result"))
                row["patient_context"] = _decode_json_phi(row.get("patient_context"))
                row = _decode_text_fields(row, ("message", "error_message"))
                jobs.append(row)
            return jobs
        except Exception as exc:
            _handle_database_error("list_recoverable_pipeline_jobs", exc)
    return [
        j for j in _MEM_JOBS.values()
        if j.get("status") in {"QUEUED", "PREPROCESSING", "TRANSCRIBING", "EXTRACTING", "TRIAGING"}
    ]


# -----------------------------------------------------------------------------
# Persistent Voice Threads (B3.1)
# -----------------------------------------------------------------------------
def save_voice_thread(thread_id: str, thread_data: dict) -> dict:
    record = {
        "id": thread_id,
        "consultation_id": thread_data.get("consultation_id"),
        "hospital_id": thread_data.get("hospital_id"),
        "patient_profile_id": thread_data.get("patient_profile_id"),
        "doctor_id": thread_data.get("doctor_id"),
        "status": thread_data.get("status", "open"),
        "messages": thread_data.get("messages", []),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    client = get_supabase_client()
    if client:
        try:
            db_record = dict(record)
            db_record["messages"] = _encode_json_phi(record["messages"])
            client.table("persistent_voice_threads").upsert(db_record).execute()
        except Exception as exc:
            _handle_database_error("save_voice_thread", exc)

    _MEM_THREADS[thread_id] = record
    return record


def get_voice_thread(thread_id: str) -> Optional[dict]:
    client = get_supabase_client()
    if client:
        try:
            res = client.table("persistent_voice_threads").select("*").eq("id", thread_id).execute()
            if res.data:
                row = dict(res.data[0])
                row["messages"] = _decode_json_phi(row.get("messages"))
                return row
        except Exception as exc:
            _handle_database_error("get_voice_thread", exc)
    return _MEM_THREADS.get(thread_id)


# -----------------------------------------------------------------------------
# Persistent Follow-ups (B3.1, B6.5)
# -----------------------------------------------------------------------------
def save_follow_up_record(follow_up_id: str, data: dict) -> dict:
    record = {
        "id": follow_up_id,
        "consultation_id": data.get("consultation_id"),
        "hospital_id": data.get("hospital_id"),
        "profile_id": data.get("profile_id"),
        "urgency_tier": data.get("urgency_tier", "self_care"),
        "scheduled_for": data.get("scheduled_for", datetime.now(timezone.utc).isoformat()),
        "status": data.get("status", "PENDING"),
        "patient_status": data.get("patient_status"),
        "patient_feedback": data.get("patient_feedback"),
        "checked_in_at": data.get("checked_in_at"),
        "created_at": data.get("created_at", datetime.now(timezone.utc).isoformat()),
    }
    client = get_supabase_client()
    if client:
        try:
            client.table("persistent_follow_ups").upsert(
                _encode_text_fields(record, ("patient_status", "patient_feedback"))
            ).execute()
        except Exception as exc:
            # If consultation is still asynchronously saving (foreign key 23503), retry after a brief delay
            if "23503" in str(exc) or "foreign key" in str(exc).lower():
                import time
                time.sleep(0.5)
                try:
                    client.table("persistent_follow_ups").upsert(
                        _encode_text_fields(record, ("patient_status", "patient_feedback"))
                    ).execute()
                except Exception as retry_exc:
                    _handle_database_error("save_follow_up", retry_exc)
            else:
                _handle_database_error("save_follow_up", exc)

    _MEM_FOLLOW_UPS[follow_up_id] = record
    return record


def get_follow_up_record(follow_up_id: str) -> Optional[dict]:
    client = get_supabase_client()
    if client:
        try:
            res = client.table("persistent_follow_ups").select("*").eq("id", follow_up_id).execute()
            if res.data:
                return _decode_text_fields(res.data[0], ("patient_status", "patient_feedback"))
        except Exception as exc:
            _handle_database_error("get_follow_up", exc)
    return _MEM_FOLLOW_UPS.get(follow_up_id)


def list_follow_up_records() -> List[dict]:
    """Return decrypted follow-up status and feedback for restricted admin reporting."""
    client = get_supabase_client()
    if client:
        try:
            res = client.table("persistent_follow_ups").select("*").order("created_at", desc=True).execute()
            if res.data:
                return [_decode_text_fields(row, ("patient_status", "patient_feedback")) for row in res.data]
        except Exception as exc:
            _handle_database_error("list_follow_ups", exc)
    return list(_MEM_FOLLOW_UPS.values())


# -----------------------------------------------------------------------------
# Append-Only Audit Logging (B3.5)
# -----------------------------------------------------------------------------
def append_audit_log_entry(
    action: str,
    user_id: str,
    role: str,
    hospital_id: Optional[str] = None,
    resource_id: Optional[str] = None,
    details: Optional[dict] = None,
    ip_address: Optional[str] = None,
) -> dict:
    record = {
        "id": str(uuid.uuid4()),
        "hospital_id": hospital_id,
        "action": action,
        "user_id": user_id,
        "role": role,
        "resource_id": resource_id,
        "details": details or {},
        "ip_address": ip_address,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    client = get_supabase_client()
    if client:
        try:
            db_record = dict(record)
            db_record["details"] = _encode_json_phi(record["details"])
            db_record = _encode_text_fields(db_record, ("ip_address",))
            client.table("audit_logs").insert(db_record).execute()
        except Exception as exc:
            _handle_database_error("append_audit_log", exc)

    _MEM_AUDIT_LOGS.append(record)
    return record


def update_audit_log_entry(log_id: str, updates: dict) -> None:
    """Audit log is immutable and strictly append-only (H2.6)."""
    raise PermissionError("Audit log is strictly append-only. Updating audit records is prohibited.")


def delete_audit_log_entry(log_id: str) -> None:
    """Audit log is immutable and strictly append-only (H2.6)."""
    raise PermissionError("Audit log is strictly append-only. Deleting audit records is prohibited.")


# -----------------------------------------------------------------------------
# Guest Emergency Cases (H1.3, H2.4)
# -----------------------------------------------------------------------------
def save_guest_case(case_id: str, case_data: dict) -> dict:
    """Persist guest emergency case in database with RLS (H1.3, H2.4)."""
    h_id = _safe_uuid_or_none(case_data.get("hospital_id")) or get_default_hospital_id()
    record = {
        "id": case_id,
        "patient_name": case_data.get("patient_name", "Emergency Patient"),
        "age": case_data.get("age"),
        "urgency_tier": case_data.get("urgency_tier", "emergency"),
        "chief_complaint": case_data.get("chief_complaint", "Emergency Walk-in / Intake"),
        "guest_token_hash": case_data.get("guest_token_hash", ""),
        "status": case_data.get("status", "unverified"),
        "hospital_id": h_id,
        "latitude": case_data.get("latitude"),
        "longitude": case_data.get("longitude"),
        "created_at": case_data.get("created_at", datetime.now(timezone.utc).isoformat()),
    }

    for k, v in case_data.items():
        if k not in record:
            record[k] = v
    client = get_supabase_client()
    if client:
        try:
            db_record = {
                key: record.get(key)
                for key in (
                    "id", "patient_name", "age", "urgency_tier", "chief_complaint",
                    "guest_token_hash", "status", "hospital_id", "created_at", "verified_at", "account_id",
                )
            }
            db_record = {key: value for key, value in db_record.items() if value is not None or key in ("age", "verified_at", "account_id")}
            if record.get("age") is not None:
                from app.security.phi_protection import encrypt_phi_field
                db_record["age_ciphertext"] = encrypt_phi_field(str(record["age"]))
                db_record["age"] = None
            location = {key: record.get(key) for key in ("latitude", "longitude") if record.get(key) is not None}
            if location:
                db_record["location_ciphertext"] = _encode_encrypted_json(location)
            client.table("guest_cases").insert(
                _encode_text_fields(db_record, ("patient_name", "chief_complaint", "urgency_tier"))
            ).execute()
        except Exception as exc:
            _handle_database_error("save_guest_case", exc)

    _MEM_GUEST_CASES[case_id] = record
    return record


def get_guest_case(case_id: str) -> Optional[dict]:
    client = get_supabase_client()
    if client:
        try:
            res = client.table("guest_cases").select("*").eq("id", case_id).execute()
            if res.data:
                row = _decode_text_fields(res.data[0], ("patient_name", "chief_complaint", "urgency_tier"))
                age_ciphertext = row.get("age_ciphertext")
                if age_ciphertext:
                    from app.security.phi_protection import decrypt_phi_field
                    row["age"] = int(decrypt_phi_field(age_ciphertext))
                location = _decode_encrypted_json(row.get("location_ciphertext"))
                if location:
                    row.update(location)
                row.pop("age_ciphertext", None)
                row.pop("location_ciphertext", None)
                return row
        except Exception as exc:
            _handle_database_error("get_guest_case", exc)
    return _MEM_GUEST_CASES.get(case_id)


# -----------------------------------------------------------------------------
# Deletion Log (H4.3, H2.4)
# -----------------------------------------------------------------------------
def log_deletion_event(
    resource_type: str,
    resource_id: str,
    reason: str,
    hospital_id: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> dict:
    """Log an immutable deletion record to deletion_log (H4.3)."""
    h_id = _safe_uuid_or_none(hospital_id) or get_default_hospital_id()
    record = {
        "id": str(uuid.uuid4()),
        "hospital_id": h_id,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "reason": reason,
        "deleted_at": datetime.now(timezone.utc).isoformat(),
        "metadata": metadata or {},
    }
    client = get_supabase_client()
    if client:
        try:
            db_record = _encode_text_fields(record, ("resource_id", "reason"))
            db_record["metadata"] = _encode_json_phi(record["metadata"])
            client.table("deletion_log").insert(db_record).execute()
        except Exception as exc:
            _handle_database_error("log_deletion_event", exc)

    _MEM_DELETION_LOGS.append(record)
    return record


def get_deletion_logs() -> List[dict]:
    client = get_supabase_client()
    if client:
        try:
            res = client.table("deletion_log").select("*").order("deleted_at", desc=True).execute()
            if res.data:
                decoded = []
                for row in res.data:
                    item = dict(row)
                    item["metadata"] = _decode_json_phi(item.get("metadata"))
                    decoded.append(_decode_text_fields(item, ("resource_id", "reason")))
                sb_ids = {log["id"] for log in decoded}
                merged = decoded + [l for l in _MEM_DELETION_LOGS if l.get("id") not in sb_ids]
                return merged
        except Exception as exc:
            _handle_database_error("get_deletion_logs", exc)
    return list(_MEM_DELETION_LOGS)


# -----------------------------------------------------------------------------
# Block C: Consent, PIN, Data Export & Statutory Erasure (B4.3, B4.4, B4.5, B4.6)
# -----------------------------------------------------------------------------

_MEM_CONSENTS: List[dict] = []


def get_consent_record(
    consent_id: str,
    owner_type: str,
    owner_id: str,
    profile_id: Optional[str],
    version: str,
) -> Optional[dict]:
    """Return a current consent only when it matches owner, version and profile."""
    if not _safe_uuid_or_none(consent_id):
        return None
    client = get_supabase_client()
    if client:
        if owner_type not in ("patient_account", "guest_case") or not _safe_uuid_or_none(owner_id):
            return None
        try:
            res = (
                client.table("consents")
                .select("id,owner_type,owner_id,profile_id,version,is_granted")
                .eq("id", consent_id)
                .eq("owner_type", owner_type)
                .eq("owner_id", owner_id)
                .eq("version", version)
                .eq("is_granted", True)
                .limit(1)
                .execute()
            )
            if res.data:
                row = res.data[0]
                if row.get("profile_id") == profile_id:
                    return row
        except Exception as exc:
            _handle_database_error("get_consent_record", exc)
    return next((
        row for row in reversed(_MEM_CONSENTS)
        if row.get("id") == consent_id
        and row.get("owner_type") == owner_type
        and row.get("owner_id") == owner_id
        and row.get("profile_id") == profile_id
        and row.get("version") == version
        and row.get("is_granted") is True
    ), None)


def save_consent_record(data: dict) -> dict:
    """Save versioned patient consent record to database (B4.5)."""
    record = {
        "id": data.get("id") or str(uuid.uuid4()),
        "patient_id": data.get("patient_id"),
        "owner_type": data.get("owner_type"),
        "owner_id": data.get("owner_id"),
        "account_id": data.get("account_id"),
        "guest_case_id": data.get("guest_case_id"),
        "consent_type": data.get("consent_type", "TELEMEDICINE_TRIAGE"),
        "version": data.get("version", "2026.11.21-v2"),
        "language_code": data.get("language_code", "en-IN"),
        "ip_hash": data.get("ip_hash"),
        "profile_id": data.get("profile_id"),
        "is_granted": data.get("is_granted", True),
        "granted_at": data.get("granted_at") or datetime.now(timezone.utc).isoformat(),
    }
    client = get_supabase_client()
    if client:
        # patient_id belongs to the legacy `patients` table, not to accounts or
        # guest cases. New consent rows use explicit owner links instead.
        record.pop("patient_id", None)
        try:
            client.table("consents").insert(record).execute()
        except Exception as exc:
            _handle_database_error("save_consent", exc)

    _MEM_CONSENTS.append(record)
    return record


def verify_profile_pin(profile_id: str, pin: str) -> bool:
    """Verify 4-digit PIN for private patient profile (B4.3)."""
    profile = get_patient_profile(profile_id)
    if not profile or not profile.get("pin_hash"):
        return True  # No PIN protection set
    return verify_password(pin.strip(), profile["pin_hash"])


def export_account_data(account_id: str) -> dict:
    """Export all profiles, consultations, follow-ups, and consents for an account (B4.6)."""
    profiles = get_profiles_for_account(account_id)
    profile_ids = [p["id"] for p in profiles]

    consultations: List[dict] = []
    follow_ups: List[dict] = []
    consents: List[dict] = []

    client = get_supabase_client()
    if client:
        if profile_ids:
            try:
                res_c = client.table("consultations").select("*").in_("profile_id", profile_ids).execute()
                if res_c.data:
                    consultations = [_migrate_consultation_row(client, row) for row in res_c.data]
            except Exception as exc:
                _handle_database_error("export_consultations", exc)

        if profile_ids:
            try:
                res_fu = client.table("persistent_follow_ups").select("*").in_("profile_id", profile_ids).execute()
                if res_fu.data:
                    follow_ups = [
                        _decode_text_fields(row, ("patient_status", "patient_feedback"))
                        for row in res_fu.data
                    ]
            except Exception as exc:
                _handle_database_error("export_follow_ups", exc)

        try:
            by_account = client.table("consents").select("*").eq("account_id", account_id).execute()
            rows = list(by_account.data or [])
            if profile_ids:
                by_profile = client.table("consents").select("*").in_("profile_id", list(profile_ids)).execute()
                rows.extend(by_profile.data or [])
            seen_ids = set()
            consents = []
            for row in rows:
                if row.get("id") in seen_ids:
                    continue
                seen_ids.add(row.get("id"))
                consents.append(_decode_text_fields(row, ("ip_address",)))
        except Exception as exc:
            _handle_database_error("export_consents", exc)

    if not follow_ups:
        follow_ups = [fu for fu in _MEM_FOLLOW_UPS.values() if fu.get("profile_id") in profile_ids]

    memory_consents = [
        c for c in _MEM_CONSENTS
        if c.get("profile_id") in profile_ids or c.get("account_id") == account_id
        or c.get("owner_id") == account_id
    ]
    existing_consent_ids = {consent.get("id") for consent in consents}
    consents.extend(consent for consent in memory_consents if consent.get("id") not in existing_consent_ids)

    return {
        "account_id": account_id,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "profiles": profiles,
        "consultations": consultations,
        "follow_ups": follow_ups,
        "consents": consents,
    }


def erase_account_data(account_id: str, reason: str = "Statutory erasure under DPDP Act 2023") -> bool:
    """Anonymize patient profiles and disassociate retained clinical records."""
    profiles = get_profiles_for_account(account_id)
    profile_ids = {str(profile["id"]) for profile in profiles}
    client = get_supabase_client()

    if client:
        try:
            from app.security.phi_protection import encrypt_phi_field
            response = client.rpc("erase_patient_account_atomic", {
                "p_account_id": account_id,
                "p_anonymized_phone": encrypt_phi_field(f"erased-account:{uuid.uuid4()}"),
            }).execute().data
            if isinstance(response, list):
                response = response[0] if response else None
            if not isinstance(response, dict) or not response.get("success"):
                raise RuntimeError("Patient account erasure did not complete")
        except Exception as exc:
            logger.error("Atomic patient account erasure failed (%s)", type(exc).__name__)
            raise RuntimeError("Patient account erasure failed; no success was recorded.") from exc

    anonymized = {
        "full_name": "ANONYMIZED_PATIENT",
        "age": None,
        "gender": None,
        "guardian_name": None,
        "allergies": [],
        "medications": [],
        "known_conditions": [],
        "pin_hash": None,
        "caregiver_relationship": None,
        "account_id": None,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    for profile in profiles:
        profile_id = str(profile["id"])
        if profile_id in _MEM_PROFILES:
            _MEM_PROFILES[profile_id].update(anonymized)

    for follow_up in _MEM_FOLLOW_UPS.values():
        if str(follow_up.get("profile_id")) in profile_ids:
            follow_up.update({"profile_id": None, "patient_status": None, "patient_feedback": None})
    for consent in _MEM_CONSENTS:
        if consent.get("account_id") == account_id or consent.get("owner_id") == account_id:
            consent.update({"profile_id": None, "account_id": None, "owner_id": None, "owner_type": None})
    for thread_id, thread in list(_MEM_THREADS.items()):
        if str(thread.get("patient_profile_id")) in profile_ids:
            del _MEM_THREADS[thread_id]
    for consultation in _MEM_CONSULTATIONS.values():
        if consultation.get("account_id") == account_id or str(consultation.get("profile_id")) in profile_ids:
            consultation.update({
                "account_id": None,
                "profile_id": None,
                "patient_name": "ANONYMIZED_PATIENT",
                "guest_name": "ANONYMIZED_PATIENT",
                "phone_number": None,
            })
    for guest_case in _MEM_GUEST_CASES.values():
        if guest_case.get("account_id") == account_id:
            guest_case["account_id"] = None
            guest_case["patient_name"] = "ANONYMIZED_PATIENT"
    for phone, account in list(_MEM_ACCOUNTS.items()):
        if account.get("id") == account_id:
            del _MEM_ACCOUNTS[phone]

    if not client:
        log_deletion_event(
            resource_type="patient_account",
            resource_id=account_id,
            reason="Patient account data erased or anonymized",
        )

    append_audit_log_entry(
        action="DATA_ERASURE_COMPLETED",
        user_id=account_id,
        role="patient",
        resource_id=account_id,
        details={"reason": reason},
    )
    return True


def reset_recycled_phone_number(account_id: str) -> bool:
    """Retire a recycled phone account so its previous owner cannot retain access."""
    profiles = get_profiles_for_account(account_id)
    profile_ids = {str(profile["id"]) for profile in profiles}
    client = get_supabase_client()
    if client:
        try:
            from app.security.phi_protection import encrypt_phi_field
            response = client.rpc("erase_patient_account_atomic", {
                "p_account_id": account_id,
                "p_anonymized_phone": encrypt_phi_field(f"recycled-account:{uuid.uuid4()}"),
            }).execute().data
            if isinstance(response, list):
                response = response[0] if response else None
            if not isinstance(response, dict) or not response.get("success"):
                raise RuntimeError("Recycled phone account retirement did not complete")
        except Exception as exc:
            logger.error("Recycled phone account retirement failed (%s)", type(exc).__name__)
            raise RuntimeError("Recycled phone account could not be safely reset.") from exc

    # Keep local development/test mirrors consistent with the atomic database operation.
    anonymized = {
        "full_name": "ANONYMIZED_PATIENT", "age": None, "gender": None,
        "guardian_name": None, "allergies": [], "medications": [],
        "known_conditions": [], "pin_hash": None,
        "caregiver_relationship": None, "account_id": None,
    }
    for profile in profiles:
        profile_id = str(profile["id"])
        if profile_id in _MEM_PROFILES:
            _MEM_PROFILES[profile_id].update(anonymized)

    for follow_up in _MEM_FOLLOW_UPS.values():
        if str(follow_up.get("profile_id")) in profile_ids:
            follow_up.update({"profile_id": None, "patient_status": None, "patient_feedback": None})
    for consent in _MEM_CONSENTS:
        if consent.get("account_id") == account_id or consent.get("owner_id") == account_id:
            consent.update({"profile_id": None, "account_id": None, "owner_id": None, "owner_type": None})
    for thread_id, thread in list(_MEM_THREADS.items()):
        if str(thread.get("patient_profile_id")) in profile_ids:
            del _MEM_THREADS[thread_id]
    for consultation in _MEM_CONSULTATIONS.values():
        if consultation.get("account_id") == account_id or str(consultation.get("profile_id")) in profile_ids:
            consultation.update({
                "account_id": None,
                "profile_id": None,
                "patient_name": "ANONYMIZED_PATIENT",
                "guest_name": "ANONYMIZED_PATIENT",
                "phone_number": None,
            })
    for guest_case in _MEM_GUEST_CASES.values():
        if guest_case.get("account_id") == account_id:
            guest_case.update({"account_id": None, "patient_name": "ANONYMIZED_PATIENT"})
    for phone, account in list(_MEM_ACCOUNTS.items()):
        if account.get("id") == account_id:
            del _MEM_ACCOUNTS[phone]

    append_audit_log_entry(
        action="RECYCLED_NUMBER_RESET",
        user_id=account_id,
        role="patient",
        resource_id=account_id,
        details={"message": "Prior account retired, profiles anonymized, and retained clinical records disassociated for recycled phone number."},
    )
    return True


# -----------------------------------------------------------------------------
# Consultations & Queue Management (B3.1, B5.1 - B5.8, F1.1)
# -----------------------------------------------------------------------------
TIER_PRIORITY_ORDER = {
    "emergency": 0,
    "doctor_today": 1,
    "doctor_soon": 2,
    "self_care": 3,
}


def save_consultation(consultation_id: str, data: dict) -> dict:
    """Save consultation record with full triage results and department mapping (B3.1, B5.3)."""
    now_iso = datetime.now(timezone.utc).isoformat()
    orig_tx = data.get("original_transcript") or data.get("transcript_original") or ""
    eng_tx = data.get("english_transcript") or data.get("transcript_english") or ""
    phone = data.get("phone_number") or ""
    settings = get_settings()
    h_id = _safe_uuid_or_none(data.get("hospital_id")) or _safe_uuid_or_none(settings.default_hospital_id)
    if not h_id and settings.is_production:
        raise RuntimeError("Consultation cannot be saved without a configured hospital ID.")
    if not h_id:
        h_id = _MEM_HOSPITALS[0]["id"]

    prof_id = data.get("profile_id")
    acc_id = data.get("account_id")
    p_name = data.get("patient_name") or data.get("guest_name") or ""
    age = data.get("age")
    gender = data.get("gender")

    if prof_id:
        prof = get_patient_profile(prof_id)
        if prof:
            if not p_name:
                p_name = prof.get("full_name") or ""
            if not age:
                age = prof.get("age")
            if not gender:
                gender = prof.get("gender")

    if not phone and acc_id:
        acc = get_patient_account_by_id(acc_id)
        if acc:
            phone = acc.get("phone_number") or ""

    stated_name = data.get("stated_patient_name")
    if not stated_name and isinstance(data.get("full_result"), dict):
        cs = data["full_result"].get("clinical_summary") or {}
        if isinstance(cs, dict):
            stated_name = cs.get("stated_patient_name")

    if not p_name:
        if stated_name:
            p_name = stated_name
        elif data.get("urgency_tier") == "emergency" or data.get("is_guest_emergency"):
            p_name = "Emergency Patient"
        else:
            p_name = "Patient"

    record = {
        "id": consultation_id,
        "hospital_id": h_id,
        "account_id": acc_id,
        "profile_id": prof_id,
        "patient_id": data.get("patient_id"),
        "patient_name": p_name,
        "guest_name": p_name,
        "phone_number": phone,
        "age": age,
        "gender": gender,
        "stated_patient_name": stated_name,
        "status": data.get("status", "triage"),
        "patient_language": data.get("patient_language", "en-IN"),
        "urgency_tier": data.get("urgency_tier", "doctor_soon"),
        "department_id": data.get("department_id"),
        "assigned_doctor_id": data.get("assigned_doctor_id"),
        "original_transcript": orig_tx,
        "transcript_original": orig_tx,
        "english_transcript": eng_tx,
        "transcript_english": eng_tx,
        "audio_url": data.get("audio_url", ""),
        "complaint_category": data.get("complaint_category"),
        "chief_complaint": data.get("chief_complaint"),
        "full_result": data.get("full_result"),
        "prescriptions": data.get("prescriptions", []),
        "prescribed_by": data.get("prescribed_by"),
        "doctor_registration_number": data.get("doctor_registration_number"),
        "prescription_timestamp": data.get("prescription_timestamp"),
        "referral_hospital": data.get("referral_hospital"),
        "latitude": data.get("latitude"),
        "longitude": data.get("longitude"),
        "created_at": data.get("created_at", now_iso),
        "updated_at": now_iso,
    }

    # Preserve any additional dynamic properties
    for k, v in data.items():
        if k not in record:
            record[k] = v

    client = get_supabase_client()
    if client and _safe_uuid_or_none(consultation_id):
        db_record = {
            "id": consultation_id,
            "hospital_id": h_id,
            "account_id": _safe_uuid_or_none(data.get("account_id")),
            "profile_id": _safe_uuid_or_none(data.get("profile_id")),
            "patient_id": data.get("patient_id"),
            "status": record["status"],
            "patient_language": record["patient_language"],
            "urgency_tier": record["urgency_tier"],
            "department_id": _safe_uuid_or_none(record["department_id"]),
            "assigned_doctor_id": record["assigned_doctor_id"],
            "original_transcript": orig_tx,
            "english_transcript": eng_tx,
            "audio_url": record["audio_url"],
            "complaint_category": record["complaint_category"],
            "chief_complaint": record["chief_complaint"],
            "full_result": record["full_result"],
            "prescriptions": record["prescriptions"],
            "prescribed_by": record["prescribed_by"],
            "doctor_registration_number": record["doctor_registration_number"],
            "prescription_timestamp": record["prescription_timestamp"],
            "referral_hospital": record["referral_hospital"],
            "created_at": record["created_at"],
            "updated_at": record["updated_at"],
        }
        try:
            client.table("consultations").upsert(_encode_consultation_row(db_record)).execute()
        except Exception as exc:
            _handle_database_error("save_consultation", exc)

    _MEM_CONSULTATIONS[consultation_id] = record
    return record


def get_consultation(consultation_id: str) -> Optional[dict]:
    """Retrieve consultation record by ID."""
    client = get_supabase_client()
    if client and _safe_uuid_or_none(consultation_id):
        try:
            res = client.table("consultations").select("*").eq("id", consultation_id).execute()
            if res.data:
                return _migrate_consultation_row(client, res.data[0])
        except Exception as exc:
            _handle_database_error("get_consultation", exc)
    return _MEM_CONSULTATIONS.get(consultation_id)


def get_consultations_for_patient(
    account_id: Optional[str] = None,
    profile_id: Optional[str] = None,
) -> List[dict]:
    """
    Retrieve all consultation history records belonging to a verified phone account or specific profile.
    Used by /api/v1/patient/consultations to display patient history (B4.2).
    """
    can_query_supabase = False
    if profile_id and _safe_uuid_or_none(profile_id):
        can_query_supabase = True
    elif account_id and _safe_uuid_or_none(account_id):
        can_query_supabase = True

    client = get_supabase_client()
    if client and can_query_supabase:
        try:
            query = client.table("consultations").select("*")
            if account_id:
                query = query.eq("account_id", account_id)
            if profile_id:
                query = query.eq("profile_id", profile_id)
            res = query.order("created_at", desc=True).execute()
            if res.data:
                return [_migrate_consultation_row(client, row) for row in res.data]
        except Exception as exc:
            _handle_database_error("get_consultations_for_patient", exc)

    items = list(_MEM_CONSULTATIONS.values())
    if account_id and profile_id:
        items = [c for c in items if c.get("account_id") == account_id and c.get("profile_id") == profile_id]
    elif profile_id:
        items = [c for c in items if c.get("profile_id") == profile_id]
    elif account_id:
        items = [c for c in items if c.get("account_id") == account_id]
    items.sort(key=lambda c: c.get("created_at", ""), reverse=True)
    return items


def list_consultations_for_queue(
    department_code: Optional[str] = None,
    tier: Optional[str] = None,
    doctor_id: Optional[str] = None,
    status: Optional[str] = None,
    search: Optional[str] = None,
    hospital_id: Optional[str] = None,
) -> List[dict]:
    """
    List consultations for doctor dashboard queue, sorted urgent-first (F3.2).
    Emergency (0) -> Doctor Today (1) -> Doctor Soon (2) -> Self Care (3).
    """
    results: List[dict] = []
    client = get_supabase_client()
    if client:
        try:
            query = client.table("consultations").select("*")
            if hospital_id:
                query = query.eq("hospital_id", hospital_id)
            if status:
                query = query.eq("status", status)
            if tier and tier.lower() != "all":
                query = query.eq("urgency_tier", tier)
            if doctor_id:
                query = query.eq("assigned_doctor_id", doctor_id)
            res = query.order("created_at", desc=True).execute()
            if res.data:
                results = [_migrate_consultation_row(client, row) for row in res.data]
        except Exception as exc:
            _handle_database_error("list_consultation_queue", exc)

    seen_ids = {c["id"] for c in results}
    for mem_c in _MEM_CONSULTATIONS.values():
        if mem_c.get("id") in seen_ids:
            continue
        if hospital_id and mem_c.get("hospital_id") != hospital_id:
            continue
        if status and mem_c.get("status") != status:
            continue
        if tier and tier.lower() != "all" and mem_c.get("urgency_tier") != tier:
            continue
        if doctor_id and mem_c.get("assigned_doctor_id") != doctor_id:
            continue
        results.append(mem_c)
        seen_ids.add(mem_c.get("id"))

    if search:
        q = search.lower()
        results = [
            c for c in results
            if q in (c.get("chief_complaint") or "").lower()
            or q in (c.get("patient_language") or "").lower()
            or q in (c.get("original_transcript") or "").lower()
            or q in (c.get("english_transcript") or "").lower()
        ]

    # Attach current assignment lock status for each case
    now_utc = datetime.now(timezone.utc)
    for c in results:
        cid = c["id"]
        assignment = get_assignment_for_consultation(cid)
        if assignment:
            is_claimed = assignment.get("status") == "claimed"
            lock_exp = assignment.get("lock_expires_at")
            if is_claimed and lock_exp:
                try:
                    exp_dt = datetime.fromisoformat(lock_exp)
                    if exp_dt.tzinfo is None:
                        exp_dt = exp_dt.replace(tzinfo=timezone.utc)
                    if exp_dt < now_utc:
                        is_claimed = False
                except Exception:
                    pass
            c["assignment"] = assignment
            c["is_claimed"] = is_claimed
            c["claimed_by_id"] = assignment.get("doctor_id") if is_claimed else None
            c["claimed_by_name"] = assignment.get("locked_by_name") if is_claimed else None
            c["lock_expires_at"] = lock_exp
        else:
            c["assignment"] = None
            c["is_claimed"] = False
            c["claimed_by_id"] = None
            c["claimed_by_name"] = None
            c["lock_expires_at"] = None

        if not c.get("patient_name"):
            c["patient_name"] = c.get("guest_name") or ("Emergency Patient" if c.get("urgency_tier") == "emergency" else "Patient")

    # Sort urgent-first: emergency (0) > doctor_today (1) > doctor_soon (2) > self_care (3)
    results.sort(
        key=lambda c: (
            TIER_PRIORITY_ORDER.get(c.get("urgency_tier", "self_care"), 4),
            c.get("created_at", "")
        )
    )
    return results


def save_doctor_action(action: dict) -> dict:
    """Persist the complete clinical action so it is shared across API workers."""
    client = get_supabase_client()
    if client:
        from app.security.phi_protection import phi_lookup_hash

        details = dict(action)
        details.pop("clinical_notes", None)
        fingerprint_source = {
            key: action.get(key)
            for key in (
                "consultation_id", "doctor_id", "action_type", "clinical_notes",
                "new_priority", "referral_hospital", "referral_urgency", "prescriptions", "disposition",
            )
        }
        fingerprint = phi_lookup_hash(
            json.dumps(fingerprint_source, sort_keys=True, separators=(",", ":"), default=str),
            "doctor-action-idempotency",
        )
        db_record = {
            "id": str(uuid.uuid4()),
            "action_id": action["action_id"],
            "consultation_id": _safe_uuid_or_none(action.get("consultation_id")),
            "doctor_id": _safe_uuid_or_none(action.get("doctor_id")),
            "action_type": action["action_type"],
            "notes": action.get("clinical_notes"),
            "details": _encode_json_phi(details),
            "idempotency_fingerprint": fingerprint,
            "created_at": datetime.fromtimestamp(action["timestamp"], timezone.utc).isoformat(),
        }
        db_record = _encode_text_fields(db_record, ("notes",))
        try:
            response = client.rpc("save_doctor_action_atomic", {
                "p_action": db_record,
                "p_fingerprint": fingerprint,
            }).execute().data
            if isinstance(response, list):
                response = response[0] if response else None
            if not isinstance(response, dict) or not isinstance(response.get("row"), dict):
                raise RuntimeError("Clinical action persistence returned an invalid response")
            row = response["row"]
            if response.get("duplicate"):
                existing = (client.table("doctor_actions").select("*")
                            .eq("id", row.get("id")).limit(1).execute())
                if existing.data:
                    decoded = _decode_doctor_action_row(existing.data[0])
                    decoded["_duplicate"] = True
                    return decoded
                raise RuntimeError("Duplicate clinical action could not be loaded")
        except Exception as exc:
            _handle_database_error("save_doctor_action", exc)
            raise
    return dict(action)


def _decode_doctor_action_row(row: dict) -> dict:
    details = _decode_json_phi(row.get("details")) or {}
    details.update({
        "action_id": row.get("action_id") or str(row["id"]),
        "consultation_id": row.get("consultation_id"),
        "doctor_id": row.get("doctor_id") or "",
        "action_type": row.get("action_type"),
        "clinical_notes": _decode_text_fields(row, ("notes",)).get("notes") or "",
    })
    return details


def get_doctor_actions(consultation_id: str) -> List[dict]:
    """Read clinical actions from durable storage, including actions written by other workers."""
    client = get_supabase_client()
    if client and _safe_uuid_or_none(consultation_id):
        try:
            res = (client.table("doctor_actions").select("*")
                   .eq("consultation_id", consultation_id)
                   .order("created_at").execute())
            if res.data:
                return [_decode_doctor_action_row(row) for row in res.data]
        except Exception:
            logger.exception("Supabase get_doctor_actions failed")
            raise
    return []


def save_assignment(
    consultation_id: str,
    doctor_id: str,
    assigned_by: str = "system",
    status: str = "assigned",
    reassignment_reason: Optional[str] = None,
    locked_by_name: Optional[str] = None,
    lock_ttl_minutes: int = 15,
) -> dict:
    """Save or update consultation doctor assignment (B5.3, B5.4)."""
    now = datetime.now(timezone.utc)
    lock_expires = (now + timedelta(minutes=lock_ttl_minutes)).isoformat() if status == "claimed" else None
    # consultation_assignments.id is UUID in Postgres.
    assignment_id = str(uuid.uuid4())
    rec = {
        "id": assignment_id,
        "consultation_id": consultation_id,
        "doctor_id": doctor_id,
        "assigned_by": assigned_by,
        "status": status,
        "reassignment_reason": reassignment_reason,
        "claimed_at": now.isoformat() if status == "claimed" else None,
        "released_at": None,
        "lock_expires_at": lock_expires,
        "locked_by_name": locked_by_name,
        "created_at": now.isoformat(),
    }

    client = get_supabase_client()
    if client:
        try:
            client.table("consultation_assignments").insert(
                _encode_text_fields(rec, ("reassignment_reason", "locked_by_name"))
            ).execute()
        except Exception as exc:
            _handle_database_error("save_assignment", exc)

    _MEM_ASSIGNMENTS[consultation_id] = rec
    return rec


def get_assignment_for_consultation(consultation_id: str) -> Optional[dict]:
    """Get active assignment for consultation."""
    client = get_supabase_client()
    if client and _safe_uuid_or_none(consultation_id):
        try:
            res = (
                client.table("consultation_assignments")
                .select("*")
                .eq("consultation_id", consultation_id)
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )
            if res.data:
                return _decode_text_fields(res.data[0], ("reassignment_reason", "locked_by_name"))
        except Exception as exc:
            _handle_database_error("get_assignment", exc)
    return _MEM_ASSIGNMENTS.get(consultation_id)


def get_assignment_history(consultation_id: str) -> List[dict]:
    client = get_supabase_client()
    if client and _safe_uuid_or_none(consultation_id):
        try:
            res = (client.table("consultation_assignments").select("*")
                   .eq("consultation_id", consultation_id).order("created_at").execute())
            if res.data:
                return [_decode_text_fields(row, ("reassignment_reason", "locked_by_name")) for row in res.data]
        except Exception as exc:
            _handle_database_error("assignment_history", exc)
    latest = _MEM_ASSIGNMENTS.get(consultation_id)
    return [latest] if latest else []


_CLAIM_MUTEX = threading.Lock()


def claim_consultation_lock(
    consultation_id: str,
    doctor_id: str,
    doctor_name: str,
    lock_ttl_minutes: int = 15,
) -> Tuple[bool, str, Optional[dict]]:
    """
    Atomic claim locking (B5.4) so two doctors cannot claim the same case.
    Prevents race condition and enforces active lock timeouts.
    """
    # Production claims go through a PostgreSQL advisory lock so requests served
    # by different Uvicorn workers cannot both win the same consultation.
    settings = get_settings()
    client = get_supabase_client()
    if (
        client
        and settings.supabase_service_role_key
        and _safe_uuid_or_none(consultation_id)
        and _safe_uuid_or_none(doctor_id)
    ):
        result = client.rpc("claim_consultation_atomic", {
            "p_consultation_id": consultation_id,
            "p_doctor_id": doctor_id,
            "p_doctor_name": _encode_text_fields({"locked_by_name": doctor_name}, ("locked_by_name",))["locked_by_name"],
            "p_lock_ttl_minutes": lock_ttl_minutes,
        }).execute()
        payload = result.data
        if isinstance(payload, list):
            payload = payload[0] if payload else None
        if not isinstance(payload, dict):
            raise RuntimeError("Atomic consultation claim returned an invalid response")
        assignment = _decode_text_fields(payload.get("assignment"), ("reassignment_reason", "locked_by_name"))
        if not payload.get("success"):
            claimant = (assignment or {}).get("locked_by_name", "another clinician")
            expires = (assignment or {}).get("lock_expires_at")
            return False, f"Case already locked and claimed by {claimant}. Lock expires at {expires}.", assignment
        _MEM_ASSIGNMENTS[consultation_id] = assignment
        consultation = get_consultation(consultation_id)
        if consultation:
            consultation["assigned_doctor_id"] = doctor_id
            consultation["status"] = "in_review"
            save_consultation(consultation_id, consultation)
        append_audit_log_entry(
            action="CONSULTATION_CLAIMED", user_id=doctor_id, role="doctor",
            resource_id=consultation_id,
            details={"locked_by_name": doctor_name, "lock_ttl_minutes": lock_ttl_minutes},
        )
        return True, "Consultation case successfully claimed and locked.", assignment

    with _CLAIM_MUTEX:
        now = datetime.now(timezone.utc)
        assignment = get_assignment_for_consultation(consultation_id)

        if assignment and assignment.get("status") == "claimed":
            current_claimant = assignment.get("doctor_id")
            lock_expires_at_str = assignment.get("lock_expires_at")
            is_lock_active = True
            if lock_expires_at_str:
                try:
                    exp_dt = datetime.fromisoformat(lock_expires_at_str)
                    if exp_dt.tzinfo is None:
                        exp_dt = exp_dt.replace(tzinfo=timezone.utc)
                    if exp_dt < now:
                        is_lock_active = False
                except Exception:
                    pass

            if is_lock_active and current_claimant != doctor_id:
                claimant_name = assignment.get("locked_by_name", "another clinician")
                return False, f"Case already locked and claimed by {claimant_name}. Lock expires at {lock_expires_at_str}.", assignment

        # Lock acquired or refreshed
        new_assignment = save_assignment(
            consultation_id=consultation_id,
            doctor_id=doctor_id,
            assigned_by=doctor_name,
            status="claimed",
            locked_by_name=doctor_name,
            lock_ttl_minutes=lock_ttl_minutes,
        )

        # Update consultation status
        consultation = get_consultation(consultation_id)
        if consultation:
            consultation["assigned_doctor_id"] = doctor_id
            consultation["status"] = "in_review"
            save_consultation(consultation_id, consultation)

        append_audit_log_entry(
            action="CONSULTATION_CLAIMED",
            user_id=doctor_id,
            role="doctor",
            resource_id=consultation_id,
            details={"locked_by_name": doctor_name, "lock_ttl_minutes": lock_ttl_minutes},
        )
        return True, "Consultation case successfully claimed and locked.", new_assignment


def release_consultation_lock(
    consultation_id: str,
    doctor_id: str,
) -> Tuple[bool, str]:
    """Release claim lock on consultation so other clinicians can claim (B5.4)."""
    with _CLAIM_MUTEX:
        assignment = get_assignment_for_consultation(consultation_id)
        if not assignment or assignment.get("doctor_id") != doctor_id:
            return False, "You do not currently hold the claim lock for this consultation."

        assignment["status"] = "released"
        assignment["released_at"] = datetime.now(timezone.utc).isoformat()
        assignment["lock_expires_at"] = None

        client = get_supabase_client()
        if client:
            try:
                client.table("consultation_assignments").update({
                    "status": "released",
                    "released_at": assignment["released_at"],
                    "lock_expires_at": None,
                }).eq("id", assignment["id"]).execute()
            except Exception as exc:
                _handle_database_error("release_assignment", exc)

        _MEM_ASSIGNMENTS[consultation_id] = assignment

        consultation = get_consultation(consultation_id)
        if consultation:
            consultation["status"] = "triage"
            consultation["assigned_doctor_id"] = None
            save_consultation(consultation_id, consultation)

        append_audit_log_entry(
            action="CONSULTATION_RELEASED",
            user_id=doctor_id,
            role="doctor",
            resource_id=consultation_id,
            details={"consultation_id": consultation_id},
        )
        return True, "Claim lock released. Case returned to active department queue."


def reassign_consultation(
    consultation_id: str,
    reassigning_doctor_id: str,
    target_doctor_id: str,
    target_department_code: Optional[str],
    reason: str,
    assigning_role: str = "doctor",
) -> Tuple[bool, str, Optional[dict]]:
    """Reassign case with mandatory clinical reason logged to audit trail (B5.6, B5.7)."""
    if not reason or len(reason.strip()) < 5:
        return False, "A mandatory clinical rationale of at least 5 characters is required for reassignment.", None
    consultation = get_consultation(consultation_id)
    if not consultation:
        return False, "Consultation not found.", None
    target_doctor = find_staff_user(target_doctor_id)
    if not target_doctor or target_doctor.get("role") != "doctor":
        return False, "Selected doctor is not an eligible staff member.", None
    if consultation.get("hospital_id") and target_doctor.get("hospital_id") and consultation["hospital_id"] != target_doctor["hospital_id"]:
        return False, "Selected doctor belongs to another hospital.", None

    with _CLAIM_MUTEX:
        target_name = target_doctor.get("full_name", target_doctor_id) if target_doctor else target_doctor_id

        new_assignment = save_assignment(
            consultation_id=consultation_id,
            doctor_id=target_doctor_id,
            assigned_by=reassigning_doctor_id,
            status="assigned",
            reassignment_reason=reason.strip(),
            locked_by_name=target_name,
        )

        consultation["assigned_doctor_id"] = target_doctor_id
        if target_department_code:
            consultation["department_id"] = target_department_code
        save_consultation(consultation_id, consultation)

        append_audit_log_entry(
            action="CONSULTATION_REASSIGNED",
            user_id=reassigning_doctor_id,
            role=assigning_role,
            resource_id=consultation_id,
            details={
                "target_doctor_id": target_doctor_id,
                "target_doctor_name": target_name,
                "target_department": target_department_code,
                "reason": reason.strip(),
            },
        )
        return True, f"Case successfully reassigned to {target_name}.", new_assignment


def override_urgency_tier(
    consultation_id: str,
    doctor_id: str,
    doctor_reg_no: Optional[str],
    new_tier: str,
    reason: str,
) -> Tuple[bool, str, Optional[dict]]:
    """Override AI urgency tier with mandatory clinical reason (B5.6, B5.7)."""
    valid_tiers = ["emergency", "doctor_today", "doctor_soon", "self_care"]
    if new_tier not in valid_tiers:
        return False, f"Invalid urgency tier '{new_tier}'. Must be one of {valid_tiers}.", None

    if not reason or len(reason.strip()) < 5:
        return False, "A mandatory clinical rationale of at least 5 characters is required to override urgency tier.", None

    consultation = get_consultation(consultation_id)
    if not consultation:
        return False, f"Consultation '{consultation_id}' not found.", None

    old_tier = consultation.get("urgency_tier", "unknown")
    consultation["urgency_tier"] = new_tier
    save_consultation(consultation_id, consultation)

    # Sync follow-up record if exists
    follow_up = get_follow_up_record(consultation_id)
    if follow_up:
        follow_up["urgency_tier"] = new_tier
        save_follow_up_record(consultation_id, follow_up)

    append_audit_log_entry(
        action="URGENCY_TIER_OVERRIDDEN",
        user_id=doctor_id,
        role="doctor",
        resource_id=consultation_id,
        details={
            "old_tier": old_tier,
            "new_tier": new_tier,
            "doctor_reg_no": doctor_reg_no,
            "clinical_reason": reason.strip(),
        },
    )
    return True, f"Urgency tier overridden from '{old_tier}' to '{new_tier}'.", consultation


def update_doctor_availability(doctor_id: str, is_on_duty: bool) -> bool:
    """Doctor availability toggle (B5.1, F3.6)."""
    client = get_supabase_client()
    if client:
        try:
            client.table("staff_users").update({"is_on_duty": is_on_duty}).eq("id", doctor_id).execute()
        except Exception as exc:
            _handle_database_error("update_doctor_duty", exc)

    # Update fallback in-memory doctor if matching
    for d in _FALLBACK_STAFF.values():
        if d.get("id") == doctor_id or d.get("email") == doctor_id:
            d["is_on_duty"] = is_on_duty

    append_audit_log_entry(
        action="DOCTOR_AVAILABILITY_CHANGED",
        user_id=doctor_id,
        role="doctor",
        resource_id=doctor_id,
        details={"is_on_duty": is_on_duty},
    )
    return True


def verify_doctor_registration_by_admin(doctor_id: str, admin_user_id: str) -> bool:
    """Administrative verification of doctor registration number (B5.2, F4.2)."""
    from app.auth import validate_doctor_registration

    client = get_supabase_client()
    if client:
        try:
            target = (client.table("staff_users").select("id,doctor_registration_number")
                     .eq("id", doctor_id).eq("role", "doctor").limit(1).execute())
            if not target.data or not validate_doctor_registration(target.data[0].get("doctor_registration_number")):
                return False
            response = (client.table("staff_users")
                        .update({"is_verified_doctor": True})
                        .eq("id", doctor_id).eq("role", "doctor")
                        .select("id").execute())
            if not response.data:
                return False
        except Exception as exc:
            _handle_database_error("verify_doctor", exc)

    found = False
    for d in _FALLBACK_STAFF.values():
        if d.get("id") == doctor_id or d.get("email") == doctor_id:
            if d.get("role") != "doctor" or not validate_doctor_registration(d.get("doctor_registration_number")):
                return False
            d["is_verified_doctor"] = True
            found = True

    if not found and not client:
        return False

    append_audit_log_entry(
        action="DOCTOR_REGISTRATION_ADMIN_VERIFIED",
        user_id=admin_user_id,
        role="admin",
        resource_id=doctor_id,
        details={"verified_doctor_id": doctor_id},
    )
    return True


def get_staff_doctors(
    department_code: Optional[str] = None,
    on_duty_only: bool = False,
    hospital_id: Optional[str] = None,
) -> List[dict]:
    """Retrieve doctor roster with specialty, schedule, capacity and status (B5.1, F4.2)."""
    docs: List[dict] = []
    client = get_supabase_client()
    if client:
        try:
            q = client.table("staff_users").select("*").eq("role", "doctor")
            if hospital_id:
                q = q.eq("hospital_id", hospital_id)
            if on_duty_only:
                q = q.eq("is_on_duty", True)
            res = q.execute()
            if res.data:
                docs = res.data
        except Exception as exc:
            _handle_database_error("get_staff_doctors", exc)

    if not docs:
        from app.config import get_settings
        if get_settings().is_development:
            docs = [s for s in _FALLBACK_STAFF.values() if s.get("role") == "doctor"]
            if hospital_id:
                docs = [s for s in docs if not s.get("hospital_id") or s.get("hospital_id") == hospital_id]
            if on_duty_only:
                docs = [s for s in docs if s.get("is_on_duty", True)]

    if department_code:
        docs = [d for d in docs if d.get("department_code") == department_code or d.get("specialty", "").upper() == department_code]

    return docs


def get_unclaimed_consultations_for_escalation() -> List[dict]:
    """
    Check unclaimed-case timers per tier and trigger automatic escalation (B5.5).
      - Emergency > 5 min -> return a case requiring manual ER follow-up
      - Doctor Today > 2 hours -> escalate to duty doctor
      - Doctor Soon > 24 hours -> escalate to Doctor Today
    """
    now = datetime.now(timezone.utc)
    escalations = []
    consultations = list_consultations_for_queue(status="triage")

    for c in consultations:
        if c.get("is_claimed"):
            continue

        created_str = c.get("created_at")
        if not created_str:
            continue

        try:
            created_dt = datetime.fromisoformat(created_str)
            elapsed_seconds = (now - created_dt).total_seconds()
            tier = c.get("urgency_tier", "self_care")
            cid = c["id"]

            if tier == "emergency" and elapsed_seconds > 300: # > 5 min
                escalations.append({
                    "consultation_id": cid,
                    "tier": tier,
                    "action": "ER_ESCALATION_REQUIRED",
                    "reason": f"Emergency case unclaimed for {int(elapsed_seconds/60)} minutes. Contact the hospital ER and duty doctor; no automatic alert is configured.",
                })
            elif tier == "doctor_today" and elapsed_seconds > 7200: # > 2 hours
                c["assigned_doctor_id"] = "11111111-0008-0000-0000-000000000008" # Duty Doctor
                save_consultation(cid, c)
                escalations.append({
                    "consultation_id": cid,
                    "tier": tier,
                    "action": "DUTY_DOCTOR_ROUTED",
                    "reason": f"Doctor Today case unclaimed for {int(elapsed_seconds/3600)} hours. Added to the Duty Doctor queue; no alert notification is configured.",
                })
            elif tier == "doctor_soon" and elapsed_seconds > 86400: # > 24 hours
                c["urgency_tier"] = "doctor_today"
                save_consultation(cid, c)
                escalations.append({
                    "consultation_id": cid,
                    "tier": "doctor_today",
                    "action": "TIER_ESCALATED",
                    "reason": "Doctor Soon case unclaimed for > 24 hours. Escalated to Doctor Today tier.",
                })
        except Exception:
            continue

    return escalations


