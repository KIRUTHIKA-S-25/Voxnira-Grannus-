"""
RuralCare AI — FastAPI Application

Endpoints:
- GET  /health — Health check & configuration status
- POST /api/v1/pipeline/process-audio — Audio triage pipeline
"""
import os
import logging
import asyncio
import ipaddress
import re
from urllib.parse import urlsplit
from typing import Optional, List, Dict, Any, Union

from dotenv import load_dotenv

load_dotenv()  # populate os.environ from .env before Settings() reads it

from contextlib import asynccontextmanager
from datetime import datetime, timezone
import hashlib
import time
import uuid
from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Request, Response, Depends, status, Security
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.responses import PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from app.observability.metrics import get_metrics

from app.audio_preprocessing import (
    AudioFormatError,
    AudioPreprocessingError,
    AudioTooLongError,
    AudioTooShortError,
)
from app.config import get_settings
from app.ml_model import get_model
from app.pipeline import run_pipeline
from app.schemas import (
    PipelineResult,
    VoicePrescriptionResponse,
    CheckInRequest,
    CheckInResponse,
    VoiceThread,
    VoiceThreadMessage,
    SOSDispatchPayload,
    SOSDispatchResponse,
)
from app.services.gemini_extract import GeminiExtractionError
from app.services.sarvam_stt import SarvamSTTError, transcribe_and_translate
from app.services.translation import translate_text
from app.services.sarvam_tts import text_to_speech

from app.auth import (
    UserRole,
    AuthenticatedUser,
    create_token,
    get_current_user,
    require_role,
    require_verified_doctor,
    generate_signed_url,
    verify_signed_url,
    revoke_token,
    security_bearer,
)
from app.security.input_validation import validate_audio_upload, sanitize_filename
from app.security.rate_limiter import rate_limit_patient_intake
from app.security.audit_logger import get_audit_logger
from app.security.data_retention import get_retention_manager
from app.consent import CONSENT_VERSION, get_consent_notice, record_patient_consent
from app.regulatory import CDSCO_SAMD_DECLARATION, EMERGENCY_DISCLAIMER_TEXT, validate_abdm_fhir_bundle
from app.jobs.job_manager import get_job_manager, JobProgressResponse
from app.telehealth.clinical_actions import (
    get_clinical_action_manager,
    DoctorActionRequest,
    ActionRecord,
)
from app.telehealth.referral_slip import generate_referral_slip, ReferralSlip
from app.follow_up import get_follow_up_manager
from app.voice_threads import get_voice_thread_manager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("rural_care.api")


def _is_production_secret_missing(value: Optional[str]) -> bool:
    if not value or not value.strip():
        return True
    lowered = value.lower()
    return any(marker in lowered for marker in (
        "your", "change_me", "change me", "example", "placeholder", "replace", "set_the_", "set the",
    ))


def _is_deployed_https_origin(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        if (
            parsed.scheme != "https" or not host or parsed.username or parsed.password
            or parsed.path or parsed.query or parsed.fragment
        ):
            return False
        if host == "localhost" or host.endswith(".localhost"):
            return False
        if any(marker in host for marker in ("example", "yourdomain")) or host.endswith(".test"):
            return False
        try:
            if ipaddress.ip_address(host).is_loopback:
                return False
        except ValueError:
            pass
        parsed.port  # Validate malformed ports.
        return True
    except ValueError:
        return False


def _validate_production_settings(settings) -> None:
    """Fail startup when a production-critical integration is missing or templated."""
    if not settings.is_production:
        return

    required_values = (
        ("SUPABASE_URL", settings.supabase_url),
        ("SUPABASE_SERVICE_ROLE_KEY", settings.supabase_service_role_key),
        ("DEFAULT_HOSPITAL_ID", settings.default_hospital_id),
        ("SARVAM_API_KEY", settings.sarvam_api_key),
        ("GEMINI_API_KEY", settings.gemini_api_key),
        ("JWT_SECRET_KEY", settings.jwt_secret_key),
        ("PHI_ENCRYPTION_KEY", settings.phi_encryption_key),
        ("GRIEVANCE_OFFICER_NAME", settings.grievance_officer_name),
        ("GRIEVANCE_OFFICER_EMAIL", settings.grievance_officer_email),
    )
    for name, value in required_values:
        if _is_production_secret_missing(value):
            raise RuntimeError(f"{name} is missing or still contains a template value.")

    supabase_url = urlsplit(settings.supabase_url)
    if supabase_url.scheme != "https" or not supabase_url.hostname:
        raise RuntimeError("SUPABASE_URL must be a valid HTTPS URL in production.")
    try:
        if str(uuid.UUID(settings.default_hospital_id)) != settings.default_hospital_id.lower():
            raise ValueError("Hospital ID is not canonical")
    except (ValueError, AttributeError):
        raise RuntimeError("DEFAULT_HOSPITAL_ID must be a valid UUID for a hospital in the configured Supabase project.")
    if len(settings.jwt_secret_key) < 32:
        raise RuntimeError("JWT_SECRET_KEY must be at least 32 characters in production.")
    if len(settings.phi_encryption_key) < 32:
        raise RuntimeError("PHI_ENCRYPTION_KEY must be at least 32 characters in production.")
    if not 1 <= settings.audio_retention_hours <= 72:
        raise RuntimeError("AUDIO_RETENTION_HOURS must be between 1 and 72 in production.")
    if settings.allow_dev_otp:
        raise RuntimeError("ALLOW_DEV_OTP must be false in production.")
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", settings.grievance_officer_email.strip()):
        raise RuntimeError("GRIEVANCE_OFFICER_EMAIL must be a valid email address in production.")

    origins = [origin.strip() for origin in os.getenv("ALLOWED_ORIGINS", "").split(",") if origin.strip()]
    if not origins or any(not _is_deployed_https_origin(origin) for origin in origins):
        raise RuntimeError("ALLOWED_ORIGINS must contain only deployed HTTPS origins without paths in production.")

async def _periodic_retention_sweep_worker(interval_seconds: int = 3600):
    """Background worker that periodically purges expired audio files (H4.2)."""
    while True:
        try:
            await asyncio.sleep(interval_seconds)
            from app.security.data_retention import get_retention_manager
            purged = get_retention_manager().execute_retention_sweep()
            if purged:
                logger.info("Periodic retention sweep purged %d files.", len(purged))
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.warning("Periodic retention sweep worker error: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Verify mandatory secrets, pre-load ML model, and maintain retention sweeps."""
    settings = get_settings()
    if not settings.jwt_secret_key:
        raise RuntimeError("JWT_SECRET_KEY is mandatory and not configured. Backend refusing to start.")

    _validate_production_settings(settings)

    try:
        model = get_model()
        if model.is_available():
            logger.info("ML priority model pre-loaded successfully at startup.")
        else:
            logger.warning("ML priority model not available at startup. Will use rule engine.")
    except Exception as exc:
        logger.warning("Failed to initialize ML model at startup: %s", exc)

    # Statutory audio retention sweep on startup
    try:
        from app.security.data_retention import get_retention_manager
        purged = get_retention_manager().execute_retention_sweep()
        if purged:
            logger.info("Startup audio retention sweep purged %d files.", len(purged))
    except Exception as exc:
        logger.warning("Startup audio retention sweep error: %s", exc)

    # Recover any durable in-flight pipeline jobs across worker restarts
    try:
        from app.jobs.job_manager import get_job_manager
        asyncio.create_task(get_job_manager().recover_pending_jobs())
    except Exception as exc:
        logger.warning("Pipeline job recovery error: %s", exc)

    sweep_task = asyncio.create_task(_periodic_retention_sweep_worker())
    try:
        yield
    finally:
        sweep_task.cancel()

app = FastAPI(
    title="RuralCare AI — Medical Triage & Information Pipeline",
    description=(
        "AI processing engine: Audio Preprocessing -> Speech-to-Text (Sarvam) -> "
        "Medical Information Extraction (Gemini) -> Safety Engine -> ML Priority Prediction"
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# -- CORS: allow configured origins or local development origins --
allowed_origins_env = os.getenv("ALLOWED_ORIGINS")
origins = [o.strip() for o in allowed_origins_env.split(",") if o.strip()] if allowed_origins_env else [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    """Enforce defensive HTTP security headers across all endpoints."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(self), geolocation=()"
    response.headers["Content-Security-Policy"] = "default-src 'self'; frame-ancestors 'none';"
    if get_settings().is_production:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"
    return response


@app.middleware("http")
async def observability_middleware(request: Request, call_next):
    """Correlation ID and latency tracking middleware."""
    req_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.request_id = req_id
    get_metrics().record_request(request.url.path)
    
    start_time = time.time()
    response = await call_next(request)
    duration = time.time() - start_time
    
    get_metrics().record_latency(request.url.path, duration)
    response.headers["X-Request-ID"] = req_id
    return response


@app.get("/health")
def health() -> dict:
    """Liveness probe."""
    settings = get_settings()
    model = get_model()
    return {
        "status": "ok",
        "sarvam_key_configured": bool(settings.sarvam_api_key),
        "gemini_key_configured": bool(settings.gemini_api_key),
        "audio_preprocessing_enabled": settings.enable_audio_preprocessing,
        "ml_model_loaded": model.is_available(),
    }


@app.get("/ready")
def ready() -> dict:
    """Readiness probe validating models and critical integrations."""
    settings = get_settings()
    model = get_model()
    database_connected = not settings.is_production
    if settings.is_production:
        try:
            from app.db import get_supabase_client
            db_client = get_supabase_client()
            hospital_response = (db_client.table("hospitals").select("id")
                                 .eq("id", settings.default_hospital_id).limit(1).execute())
            if not hospital_response.data:
                raise RuntimeError("Configured default hospital was not found in Supabase.")
            database_connected = True
        except Exception as exc:
            logger.error("Database readiness check failed (%s)", type(exc).__name__)

    is_ready = bool(settings.sarvam_api_key) and bool(settings.gemini_api_key) and database_connected
    payload = {
        "status": "ready" if is_ready else "degraded",
        "sarvam_configured": bool(settings.sarvam_api_key),
        "gemini_configured": bool(settings.gemini_api_key),
        "database_connected": database_connected,
        "ml_model_loaded": model.is_available(),
    }
    if not is_ready:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=payload)
    return payload


@app.get("/metrics", response_class=PlainTextResponse)
def prometheus_metrics():
    """Prometheus exposition metrics."""
    return get_metrics().generate_prometheus_output()


# --- Security & Auth Endpoints ---

class OTPRequest(BaseModel):
    phone_number: str


class OTPRequestResponse(BaseModel):
    success: bool
    message: str


class OTPVerifyRequest(BaseModel):
    phone_number: str
    otp_code: str


class PatientPinStatusResponse(BaseModel):
    phone_number: str
    exists: bool
    has_pin: bool
    is_locked: bool
    remaining_lockout_seconds: int = 0


class PatientSetPinRequest(BaseModel):
    phone_number: str
    pin: str


class PatientLoginRequest(BaseModel):
    phone_number: str
    pin: str


class GuestEmergencyRequest(BaseModel):
    guest_name: str
    age: Optional[Union[str, int]] = None
    gender: Optional[str] = None
    language_code: Optional[str] = "en-IN"
    latitude: Optional[float] = None
    longitude: Optional[float] = None


class GuestEmergencyResponse(BaseModel):
    success: bool
    guest_token: str
    case_id: str
    message: str
    emergency_contact: str = "108 / 112"


class LoginRequest(BaseModel):
    user_id: Optional[str] = None
    email: Optional[str] = None
    role: Optional[UserRole] = None
    password: Optional[str] = None
    doctor_registration_number: Optional[str] = None
    state_medical_council: Optional[str] = None


class LoginResponse(BaseModel):
    token: str
    user_id: str
    role: UserRole
    is_verified_doctor: bool = False
    doctor_registration_number: Optional[str] = None
    phone_number: Optional[str] = None
    account_id: Optional[str] = None
    full_name: Optional[str] = None
    hospital_id: Optional[str] = None


class PatientProfileCreate(BaseModel):
    full_name: str
    age: Optional[Union[str, int]] = None
    gender: Optional[str] = None
    relation: Optional[str] = "self"
    preferred_language: Optional[str] = "ta-IN"
    allergies: Optional[List[str]] = None
    medications: Optional[List[str]] = None
    known_conditions: Optional[List[str]] = None
    pin: Optional[str] = None
    guardian_name: Optional[str] = None
    is_caregiver_managed: bool = False
    caregiver_relationship: Optional[str] = None


class ConsentRequest(BaseModel):
    language_code: str = "en-IN"
    explicit_consent: bool
    profile_id: Optional[str] = None


class ErasureRequest(BaseModel):
    patient_id: str
    reason: Optional[str] = "Statutory erasure requested under DPDP Act 2023"


@app.get("/api/v1/auth/patient/phone-status", response_model=PatientPinStatusResponse)
def get_patient_phone_status_endpoint(phone_number: str):
    """
    Check if a patient phone number exists in the database, whether a 4-digit PIN
    is set, and whether the account is currently locked.
    """
    from app.db import check_patient_phone_status
    status_info = check_patient_phone_status(phone_number)
    return PatientPinStatusResponse(**status_info)


@app.post("/api/v1/auth/patient/set-pin", response_model=LoginResponse)
def set_patient_pin_endpoint(req: PatientSetPinRequest, response: Response):
    """
    Set compulsory 4-digit PIN for new patient or account without PIN.
    Persists salted PBKDF2 hash into database and establishes authenticated session.
    """
    from app.db import register_patient_pin
    success, message, account = register_patient_pin(req.phone_number, req.pin)
    if not success or not account:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=message,
        )

    account_id = account["id"]
    phone_number = account["phone_number"]
    user_id = f"pat_{account_id[:8]}"

    token = create_token(
        user_id=user_id,
        role=UserRole.PATIENT,
        phone_number=phone_number,
        account_id=account_id,
    )

    is_prod = get_settings().is_production
    response.set_cookie(
        key="grannus_auth_token",
        value=token,
        httponly=True,
        secure=is_prod,
        samesite="lax",
        path="/",
        max_age=86400,
    )

    get_audit_logger().log(
        action="AUTH_PATIENT_PIN_REGISTERED",
        user_id=user_id,
        role=UserRole.PATIENT.value,
        details={"account_id": account_id},
    )

    return LoginResponse(
        token=token,
        user_id=user_id,
        role=UserRole.PATIENT,
        phone_number=phone_number,
        account_id=account_id,
    )


@app.post("/api/v1/auth/patient/login", response_model=LoginResponse)
def login_patient_with_pin_endpoint(req: PatientLoginRequest, response: Response):
    """
    Authenticate patient using phone number + 4-digit PIN.
    Enforces maximum 5 attempts with 15-minute lockout.
    Sets httpOnly cookie on success.
    """
    from app.db import verify_patient_pin
    success, message, account = verify_patient_pin(req.phone_number, req.pin)
    if not success or not account:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=message,
        )

    account_id = account["id"]
    phone_number = account["phone_number"]
    user_id = f"pat_{account_id[:8]}"

    token = create_token(
        user_id=user_id,
        role=UserRole.PATIENT,
        phone_number=phone_number,
        account_id=account_id,
    )

    is_prod = get_settings().is_production
    response.set_cookie(
        key="grannus_auth_token",
        value=token,
        httponly=True,
        secure=is_prod,
        samesite="lax",
        path="/",
        max_age=86400,
    )

    get_audit_logger().log(
        action="AUTH_PATIENT_PIN_VERIFIED",
        user_id=user_id,
        role=UserRole.PATIENT.value,
        details={"account_id": account_id},
    )

    return LoginResponse(
        token=token,
        user_id=user_id,
        role=UserRole.PATIENT,
        phone_number=phone_number,
        account_id=account_id,
    )


@app.post("/api/v1/auth/otp/request", response_model=OTPRequestResponse)
def request_phone_otp_endpoint(
    req: OTPRequest,
    request: Request,
):
    """
    Retired compatibility endpoint. Patient sign-in uses phone number and PIN.
    """
    raise HTTPException(status_code=410, detail="SMS sign-in is disabled. Use your patient PIN to sign in.")


@app.post("/api/v1/auth/otp/verify", response_model=LoginResponse)
def verify_phone_otp_endpoint(req: OTPVerifyRequest, response: Response):
    """
    Retired compatibility endpoint. Patient sign-in uses phone number and PIN.
    """
    raise HTTPException(status_code=410, detail="SMS sign-in is disabled. Use your patient PIN to sign in.")

@app.post("/api/v1/auth/login", response_model=LoginResponse)
async def login(req: LoginRequest, response: Response):
    """
    Authenticate staff (doctors, nurses, admins) with email and password (A2),
    or verify credentials with medical registration number validation (B2.3).
    Sets httpOnly session cookie (A4).
    """
    from app.auth import validate_doctor_registration, verify_token
    from app.db import authenticate_staff_user, find_staff_user

    identifier = (req.email or req.user_id or "").strip()
    if not identifier:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email or User ID is required.",
        )

    staff = None
    staff_record = find_staff_user(identifier)
    if staff_record:
        if not req.password:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Password is required for staff account authentication.",
            )
        staff = authenticate_staff_user(identifier, req.password)
        if not staff:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials. Please verify your email and password.",
            )
        role = UserRole(staff["role"])
        user_id = staff.get("id", identifier)
        doc_reg = staff.get("doctor_registration_number") or req.doctor_registration_number
        state_council = staff.get("state_council") or req.state_medical_council
        full_name = staff.get("full_name")
        hospital_id = staff.get("hospital_id")
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials. Unregistered staff account.",
        )

    is_verified_doc = None
    if staff and role == UserRole.DOCTOR:
        # Never infer licensure from a missing DB field or registration-number format.
        is_verified_doc = staff.get("is_verified_doctor", False)

    if role == UserRole.DOCTOR:
        if not doc_reg:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Doctor registration requires a valid NMC/State Medical Council registration number.",
            )
        if not is_verified_doc and not validate_doctor_registration(doc_reg):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid medical registration number format. Must be an official NMC or State Council ID.",
            )

    token = create_token(
        user_id=user_id,
        role=role,
        doctor_reg_no=doc_reg,
        state_council=state_council,
        hospital_id=hospital_id,
        is_verified_doctor=is_verified_doc,
    )
    user = verify_token(token)

    is_prod = get_settings().is_production
    response.set_cookie(
        key="grannus_auth_token",
        value=token,
        httponly=True,
        secure=is_prod,
        samesite="lax",
        path="/",
        max_age=86400,
    )

    get_audit_logger().log(
        action="AUTH_LOGIN",
        user_id=user.user_id,
        role=user.role.value,
        doctor_registration_number=user.doctor_registration_number,
    )

    return LoginResponse(
        token=token,
        user_id=user.user_id,
        role=user.role,
        is_verified_doctor=user.is_verified_doctor,
        doctor_registration_number=user.doctor_registration_number,
        full_name=full_name,
        hospital_id=hospital_id,
    )


@app.post("/api/v1/auth/guest-emergency", response_model=GuestEmergencyResponse)
def guest_emergency_intake(req: GuestEmergencyRequest, response: Response):
    """
    Emergency access without OTP (H1.3).
    Creates a secure single-case guest intake. Patients must call 108/112 directly;
    this endpoint does not alert a hospital or dispatch emergency services.
    Sets httpOnly session cookie (A4).
    """
    if req.latitude is not None and not (-90.0 <= req.latitude <= 90.0):
        raise HTTPException(status_code=400, detail="Latitude must be between -90 and 90 degrees.")
    if req.longitude is not None and not (-180.0 <= req.longitude <= 180.0):
        raise HTTPException(status_code=400, detail="Longitude must be between -180 and 180 degrees.")

    clean_name = req.guest_name.strip()
    if len(clean_name) < 2:
        clean_name = "Emergency Patient"

    # `guest_cases.id` and consent guest_case_id links are UUID columns.
    case_id = str(uuid.uuid4())
    guest_id = f"guest_{uuid.uuid4().hex[:12]}"

    token = create_token(
        user_id=guest_id,
        role=UserRole.GUEST_EMERGENCY,
        guest_case_id=case_id,
        guest_token=guest_id,
        expires_in_seconds=7200,  # 2 hours
    )

    is_prod = get_settings().is_production
    response.set_cookie(
        key="grannus_auth_token",
        value=token,
        httponly=True,
        secure=is_prod,
        samesite="lax",
        path="/",
        max_age=7200,
    )

    from app.db import save_consultation, save_guest_case
    save_guest_case(case_id, {
        "id": case_id,
        "patient_name": clean_name,
        "age": req.age,
        "urgency_tier": "emergency",
        "chief_complaint": f"Emergency Guest Walk-in / Intake: {clean_name}",
        "guest_token_hash": hashlib.sha256(token.encode()).hexdigest(),
        "status": "unverified",
        "latitude": req.latitude,
        "longitude": req.longitude,
    })
    save_consultation(case_id, {
        "id": case_id,
        "urgency_tier": "emergency",
        "status": "emergency_unverified",
        "chief_complaint": f"Emergency Guest Walk-in / Intake: {clean_name}",
        "patient_language": req.language_code or "en-IN",
        "department_id": "GEN_MED",
        "is_guest_emergency": True,
        "guest_name": clean_name,
        "latitude": req.latitude,
        "longitude": req.longitude,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

    get_audit_logger().log(
        action="GUEST_EMERGENCY_INTAKE",
        user_id=guest_id,
        role="guest_emergency",
        consultation_id=case_id,
        details={"name": clean_name, "age": req.age, "gender": req.gender},
    )

    return GuestEmergencyResponse(
        success=True,
        guest_token=token,
        case_id=case_id,
        message="Emergency intake recorded. Call 108 or 112 now; this app does not dispatch an ambulance.",
        emergency_contact="108 / 112",
    )


@app.post("/api/v1/emergency/sos", response_model=SOSDispatchResponse)
async def dispatch_emergency_sos(
    req: SOSDispatchPayload,
    request: Request,
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """
    Records a patient SOS request and returns a clear instruction to call emergency services directly. This API
    does not connect to ambulance or emergency-service dispatch systems.
    """
    if req.latitude is not None and not (-90.0 <= req.latitude <= 90.0):
        raise HTTPException(status_code=400, detail="Latitude must be between -90 and 90 degrees.")
    if req.longitude is not None and not (-180.0 <= req.longitude <= 180.0):
        raise HTTPException(status_code=400, detail="Longitude must be between -180 and 180 degrees.")
    if current_user.role not in (UserRole.PATIENT, UserRole.GUEST_EMERGENCY):
        raise HTTPException(status_code=403, detail="Only patients may create an emergency SOS alert.")
    from app.security.rate_limiter import rate_limit_sos
    rate_limit_sos(request, current_user.user_id)

    from app.db import save_sos_dispatch
    dispatch_data = req.model_dump()
    if current_user.role == UserRole.PATIENT:
        from app.db import get_patient_account_by_id
        account = get_patient_account_by_id(current_user.account_id) if current_user.account_id else None
        dispatch_data["patient_phone"] = account.get("phone_number") if account else None
    else:
        dispatch_data["patient_phone"] = None
    record = save_sos_dispatch(dispatch_data)
    dispatch_id = record["id"]

    map_url = None
    if req.latitude is not None and req.longitude is not None:
        map_url = f"https://www.google.com/maps?q={req.latitude},{req.longitude}"

    from app.db import append_audit_log_entry
    append_audit_log_entry(
        action="EMERGENCY_SOS_TRIGGERED",
        user_id=current_user.account_id or current_user.user_id,
        role=current_user.role.value,
        resource_id=dispatch_id,
        details={
            "latitude": req.latitude,
            "longitude": req.longitude,
            "accuracy": req.accuracy,
            "patient_name": req.patient_name,
            "emergency_type": req.emergency_type,
        },
    )

    return SOSDispatchResponse(
        success=True,
        dispatch_id=dispatch_id,
        status="RECORDED",
        ambulance_number="108",
        emergency_number="112",
        location_recorded=bool(req.latitude is not None and req.longitude is not None),
        map_url=map_url,
        message="SOS request recorded. Call 108 or 112 yourself now; this app does not notify contacts or dispatch emergency services.",
        timestamp=record["created_at"],
    )


@app.post("/api/v1/auth/refresh", response_model=LoginResponse)
def refresh_session_token(response: Response, current_user: AuthenticatedUser = Depends(get_current_user)):
    """
    Renew active session token (H1.4) and update httpOnly cookie (A4).
    """
    new_token = create_token(
        user_id=current_user.user_id,
        role=current_user.role,
        doctor_reg_no=current_user.doctor_registration_number,
        state_council=current_user.state_medical_council,
        phone_number=current_user.phone_number,
        account_id=current_user.account_id,
        profile_id=current_user.profile_id,
        hospital_id=current_user.hospital_id,
        is_verified_doctor=current_user.is_verified_doctor,
        guest_case_id=current_user.guest_case_id,
        guest_token=current_user.guest_token,
        expires_in_seconds=86400,
    )

    is_prod = get_settings().is_production
    response.set_cookie(
        key="grannus_auth_token",
        value=new_token,
        httponly=True,
        secure=is_prod,
        samesite="lax",
        path="/",
        max_age=86400,
    )

    return LoginResponse(
        token=new_token,
        user_id=current_user.user_id,
        role=current_user.role,
        is_verified_doctor=current_user.is_verified_doctor,
        doctor_registration_number=current_user.doctor_registration_number,
        phone_number=current_user.phone_number,
        account_id=current_user.account_id,
    )


@app.post("/api/v1/auth/logout")
async def logout(
    response: Response,
    request: Request,
    current_user: AuthenticatedUser = Depends(get_current_user),
    auth: Optional[HTTPAuthorizationCredentials] = Security(security_bearer),
):
    """Revoke active JWT token upon logout and clear httpOnly session cookie (A4, B2.5)."""
    token = None
    if auth and auth.credentials:
        token = auth.credentials
    elif request and request.cookies.get("grannus_auth_token"):
        token = request.cookies.get("grannus_auth_token")

    if token:
        revoke_token(token)

    response.delete_cookie(key="grannus_auth_token", path="/")
    get_audit_logger().log(
        action="AUTH_LOGOUT",
        user_id=current_user.user_id,
        role=current_user.role.value,
    )
    return {"status": "success", "message": "Logged out successfully. Session invalidated."}


class CreateStaffRequest(BaseModel):
    email: str
    password: str
    role: str = "doctor"
    full_name: str
    doctor_registration_number: Optional[str] = None
    state_medical_council: Optional[str] = None
    specialty: Optional[str] = None
    department_code: Optional[str] = "GEN_MED"
    languages: Optional[List[str]] = None


@app.post("/api/v1/auth/admin/staff")
def admin_create_staff(
    req: CreateStaffRequest,
    admin: AuthenticatedUser = Depends(require_role([UserRole.ADMIN])),
):
    """
    Admin-only endpoint to register healthcare staff with unique PBKDF2 hashed passwords (A2).
    """
    from app.db import create_staff_user, find_staff_user
    if find_staff_user(req.email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Staff user with this email already exists.",
        )
    staff = create_staff_user(
        email=req.email,
        password=req.password,
        role=req.role,
        full_name=req.full_name,
        doctor_registration_number=req.doctor_registration_number,
        state_council=req.state_medical_council,
        specialty=req.specialty,
        department_code=req.department_code,
        languages=req.languages,
    )
    get_audit_logger().log(
        action="ADMIN_CREATED_STAFF",
        user_id=admin.user_id,
        role=admin.role.value,
        details={"created_email": req.email, "role": req.role},
    )
    return {
        "success": True,
        "message": f"Staff user {req.email} created successfully.",
        "id": staff["id"],
        "email": staff["email"],
        "role": staff["role"],
    }


@app.get("/api/v1/auth/me", response_model=AuthenticatedUser)
async def get_me(
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """Retrieve profile and session claims for currently authenticated user."""
    return current_user


@app.get("/api/v1/patient/profiles")
async def list_patient_profiles(
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """List all patient profiles linked to authenticated phone account (B4.1)."""
    if current_user.role != UserRole.PATIENT:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Only patient accounts can browse patient profiles.",
        )
    from app.db import get_profiles_for_account
    if not current_user.account_id:
        return []
    return get_profiles_for_account(current_user.account_id)


@app.get("/api/v1/patient/consultations")
async def list_patient_consultations(
    profile_id: Optional[str] = None,
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """
    List all clinical intake reports and consultations for the authenticated patient (B4.2).
    Optionally filters by specific patient family profile.
    """
    if current_user.role != UserRole.PATIENT:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Only patient accounts can browse their consultation history.",
        )
    if profile_id:
        from app.db import get_patient_profile
        prof = get_patient_profile(profile_id)
        if not prof:
            raise HTTPException(status_code=404, detail="Patient profile not found.")
        if prof.get("account_id") != current_user.account_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Forbidden: Cannot access consultations of another patient profile.",
            )
    from app.db import get_consultations_for_patient
    account_id = current_user.account_id
    consultations = get_consultations_for_patient(account_id=account_id, profile_id=profile_id)
    return {
        "count": len(consultations),
        "consultations": consultations,
    }


@app.post("/api/v1/patient/profiles")
async def create_patient_profile_endpoint(
    req: PatientProfileCreate,
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """Register a new patient profile under verified phone account (B4.1)."""
    if current_user.role != UserRole.PATIENT:
        raise HTTPException(status_code=403, detail="Only authenticated patient accounts may create patient profiles.")
    if not current_user.account_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Authenticated patient phone account required to register profiles.",
        )
    from app.db import create_patient_profile, hash_password
    profile_data = req.model_dump(exclude={"pin"}) if hasattr(req, "model_dump") else req.dict(exclude={"pin"})
    if req.pin:
        profile_data["pin_hash"] = hash_password(req.pin)

    profile = create_patient_profile(current_user.account_id, profile_data)
    get_audit_logger().log(
        action="CREATE_PATIENT_PROFILE",
        user_id=current_user.user_id,
        role=current_user.role.value,
        details={"profile_id": profile["id"], "relation": req.relation},
    )
    return profile


class VerifyPinRequest(BaseModel):
    pin: str


@app.get("/api/v1/patient/profiles/{profile_id}")
def get_patient_profile_endpoint(
    profile_id: str,
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """Retrieve details for a single patient profile with ownership enforcement (H3.1)."""
    if current_user.role != UserRole.PATIENT:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Only patient accounts can browse patient profiles.",
        )
    from app.db import get_patient_profile
    profile = get_patient_profile(profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Patient profile not found.")
    if profile.get("account_id") != current_user.account_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: You cannot access another account's patient profile.",
        )
    get_audit_logger().log(
        action="PATIENT_PROFILE_READ",
        user_id=current_user.user_id,
        role=current_user.role.value,
        details={"profile_id": profile_id},
    )
    return profile


@app.post("/api/v1/patient/profiles/{profile_id}/verify-pin")
def verify_profile_pin_endpoint(
    profile_id: str,
    req: VerifyPinRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """Verify 4-digit PIN before unlocking private patient profile (B4.3)."""
    if current_user.role != UserRole.PATIENT:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Only patient accounts can verify patient profile PINs.",
        )
    from app.db import get_patient_profile, verify_profile_pin
    profile = get_patient_profile(profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Patient profile not found.")
    if profile.get("account_id") != current_user.account_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: You cannot access another account's patient profile.",
        )
    if not verify_profile_pin(profile_id, req.pin):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect 4-digit PIN for this profile.",
        )
    return {"verified": True, "profile_id": profile_id}


@app.post("/api/v1/patient/account/reset-recycled")
@app.post("/api/v1/patient/reset-recycled-phone")
def reset_recycled_account(
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """Clear prior profile associations for recycled phone numbers (B4.4)."""
    if current_user.role != UserRole.PATIENT:
        raise HTTPException(status_code=403, detail="Only patient accounts may reset recycled phone history.")
    if not current_user.account_id:
        raise HTTPException(status_code=400, detail="Authenticated patient phone account required.")
    from app.db import reset_recycled_phone_number
    reset_recycled_phone_number(current_user.account_id)
    return {"status": "success", "message": "Recycled phone number profile history reset successfully."}


class PatientErasureRequest(BaseModel):
    reason: Optional[str] = "Statutory erasure requested under DPDP Act 2023"


@app.post("/api/v1/patient/account/erasure")
def request_patient_data_erasure(
    req: PatientErasureRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """Erase the authenticated patient's account through the atomic data layer."""
    if current_user.role != UserRole.PATIENT:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Only patients can request statutory account erasure.",
        )
    if not current_user.account_id:
        raise HTTPException(status_code=400, detail="Authenticated patient account required for data erasure.")
    from app.db import erase_account_data
    try:
        erase_account_data(
            current_user.account_id,
            reason=req.reason or "Statutory erasure requested under DPDP Act 2023",
        )
    except RuntimeError as exc:
        logger.error("Patient erasure request failed for authenticated account")
        raise HTTPException(status_code=503, detail="Patient data erasure could not be completed. Please retry.") from exc
    return {
        "status": "success",
        "message": "Patient account data was erased or anonymized according to retention requirements.",
    }


@app.get("/api/v1/consent/notice")
def consent_notice(language_code: str = "en-IN"):
    """Fetch the localized consent notice and configured grievance contact."""
    return get_consent_notice(language_code)


@app.post("/api/v1/consent/record")
def record_consent(
    req: ConsentRequest,
    request: Request,
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """Store timestamped, explicit patient consent before audio processing (B4.5)."""
    if current_user.role not in (UserRole.PATIENT, UserRole.GUEST_EMERGENCY):
        raise HTTPException(status_code=403, detail="Only a patient may record consent for their own consultation.")

    owner_type = "patient_account" if current_user.role == UserRole.PATIENT else "guest_case"
    owner_id = current_user.account_id if owner_type == "patient_account" else current_user.guest_case_id
    if not owner_id and get_settings().is_production:
        raise HTTPException(status_code=403, detail="A valid patient account or guest case is required to record consent.")
    # Accountless patient tokens exist only in isolated development/test flows.
    owner_id = owner_id or current_user.user_id
    if not owner_id:
        raise HTTPException(status_code=403, detail="A patient account or guest case is required to record consent.")
    if req.profile_id:
        if current_user.role != UserRole.PATIENT or not current_user.account_id:
            raise HTTPException(status_code=403, detail="A patient profile must belong to an authenticated patient account.")
        from app.db import get_patient_profile
        profile = get_patient_profile(req.profile_id)
        if not profile or profile.get("account_id") != current_user.account_id:
            raise HTTPException(status_code=403, detail="The selected patient profile does not belong to this account.")

    client_ip = request.client.host if request.client else "127.0.0.1"
    try:
        record = record_patient_consent(
            patient_id=owner_id,
            language_code=req.language_code,
            client_ip=client_ip,
            explicit_consent=req.explicit_consent,
        )
        from app.db import save_consent_record
        save_consent_record({
            "id": record.consent_id,
            "patient_id": None,
            "owner_type": owner_type,
            "owner_id": owner_id,
            "account_id": current_user.account_id if owner_type == "patient_account" else None,
            "guest_case_id": current_user.guest_case_id if owner_type == "guest_case" else None,
            "version": record.consent_version,
            "language_code": req.language_code,
            "ip_hash": record.ip_hash,
            "profile_id": req.profile_id,
            "is_granted": True,
        })
        get_audit_logger().log(
            action="RECORD_CONSENT",
            user_id=owner_id,
            role="patient",
            details={"consent_id": record.consent_id, "version": record.consent_version},
        )
        return record
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _require_current_audio_consent(
    consent_id: Optional[str], current_user: AuthenticatedUser, profile_id: Optional[str]
) -> None:
    """Require a persisted current-version consent belonging to this patient and profile."""
    if not consent_id:
        raise HTTPException(status_code=428, detail="Record consent before submitting patient audio.")
    if current_user.role not in (UserRole.PATIENT, UserRole.GUEST_EMERGENCY):
        raise HTTPException(status_code=403, detail="Only a patient may submit patient audio.")
    owner_type = "patient_account" if current_user.role == UserRole.PATIENT else "guest_case"
    owner_id = current_user.account_id if owner_type == "patient_account" else current_user.guest_case_id
    if not owner_id and get_settings().is_production:
        raise HTTPException(status_code=403, detail="A valid patient account or guest case is required for audio submission.")
    owner_id = owner_id or current_user.user_id
    from app.db import get_consent_record
    if not get_consent_record(consent_id, owner_type, owner_id, profile_id, CONSENT_VERSION):
        raise HTTPException(status_code=403, detail="Consent is missing, outdated, or does not match this patient and profile.")


@app.get("/api/v1/patient/data-export")
def export_patient_data(
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """Statutory Right to Data Portability & Access under DPDP Act 2023 (B4.6)."""
    if current_user.role != UserRole.PATIENT:
        raise HTTPException(status_code=403, detail="Only patient accounts may export patient data.")
    if not current_user.account_id:
        raise HTTPException(status_code=400, detail="Authenticated account required for data export.")
    from app.db import export_account_data
    data = export_account_data(current_user.account_id)
    get_audit_logger().log(
        action="PATIENT_DATA_EXPORT",
        user_id=current_user.user_id,
        role=current_user.role.value,
        details={"account_id": current_user.account_id},
    )
    return data


@app.post("/api/v1/patient/data-erasure")
def erase_patient_data(
    req: Optional[ErasureRequest] = None,
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """Statutory Right to Erasure under Section 12 of DPDP Act 2023 (B4.6)."""
    if current_user.role != UserRole.PATIENT:
        raise HTTPException(status_code=403, detail="Only patient accounts may request data erasure.")
    if not current_user.account_id:
        raise HTTPException(status_code=400, detail="Authenticated account required for data erasure.")
    from app.db import erase_account_data
    reason = req.reason if req and req.reason else "Statutory erasure under DPDP Act 2023"
    erase_account_data(current_user.account_id, reason=reason)
    return {"status": "success", "message": "All patient profiles and identifiable records erased."}


@app.post("/api/v1/patient/request-erasure")
def request_erasure(req: ErasureRequest, current_user: AuthenticatedUser = Depends(get_current_user)):
    """Legacy alias that erases only the authenticated patient's own account."""
    if current_user.role != UserRole.PATIENT:
        raise HTTPException(status_code=403, detail="Only patients may request erasure of their own account.")
    if not current_user.account_id:
        raise HTTPException(status_code=400, detail="Authenticated patient account required for data erasure.")
    if req.patient_id not in (current_user.account_id, current_user.user_id):
        raise HTTPException(status_code=403, detail="The requested patient record does not belong to this account.")

    from app.db import erase_account_data
    try:
        erase_account_data(
            current_user.account_id,
            reason=req.reason or "Statutory erasure requested under DPDP Act 2023",
        )
    except RuntimeError as exc:
        logger.error("Patient erasure request failed for authenticated account")
        raise HTTPException(status_code=503, detail="Patient data erasure could not be completed. Please retry.") from exc

    get_audit_logger().log(
        action="REQUEST_ERASURE",
        user_id=current_user.account_id,
        role=current_user.role.value,
        details={"account_id": current_user.account_id, "reason": req.reason},
    )
    return {
        "status": "success",
        "message": "Patient account data was erased or anonymized according to retention requirements.",
    }


@app.get("/api/v1/regulatory/positioning")
def regulatory_positioning():
    """Statutory CDSCO SaMD and Telemedicine Practice Guidelines compliance statement."""
    return {
        "cdsco_declaration": CDSCO_SAMD_DECLARATION,
        "emergency_disclaimer": EMERGENCY_DISCLAIMER_TEXT,
        "telemedicine_guidelines_compliant": False,  # Pending formal counsel confirmation (B4)
    }


@app.get("/api/v1/media/stream")
def stream_media(
    file: Optional[str] = None,
    expires: Optional[int] = None,
    sig: Optional[str] = None,
    request: Request = None,
    auth: Optional[HTTPAuthorizationCredentials] = Security(security_bearer),
):
    """
    Access short-lived cryptographic signed media URL (prevents unauthorized public access).
    Validates token or signature before parameter validation to return 401 on missing auth (C3).
    """
    has_token = bool((auth and auth.credentials) or (request and request.cookies.get("grannus_auth_token")))
    if not has_token and not sig:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication token or valid signature is required to access media stream.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not file or expires is None or not sig:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing required stream parameters (file, expires, sig).",
        )
    if not verify_signed_url(file, expires, sig):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Signed media link is invalid or expired. Please re-authenticate.",
        )
    # Block H4.7: If audio file has expired/deleted under retention policy -> 404
    if get_retention_manager().is_audio_purged(file) or not os.path.exists(file):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Audio recording has expired or been permanently deleted under the statutory retention policy.",
        )
    return {"status": "authorized", "file": file, "valid_until": expires}


@app.get("/api/v1/hospitals")
def list_hospitals_endpoint():
    """Retrieve hospital and healthcare network details."""
    from app.db import get_hospitals
    return {"hospitals": get_hospitals()}


@app.get("/api/v1/departments")
def list_departments_endpoint(hospital_id: Optional[str] = None):
    """Retrieve hospital clinical departments and specialties."""
    from app.db import get_departments
    return {"departments": get_departments(hospital_id)}


# --- Core Pipeline Endpoints ---

@app.post("/api/v1/pipeline/process-audio", response_model=PipelineResult)
async def process_audio(
    request: Request,
    audio: UploadFile = File(..., description="Patient's voice recording (WAV, MP3, WEBM, etc.)"),
    language_code: str = Form(
        "unknown", description="BCP-47 code e.g. 'ta-IN', 'hi-IN', or 'unknown' to auto-detect"
    ),
    doctor_preferred_language: str = Form(
        "en-IN", description="BCP-47 code for the doctor's interface language"
    ),
    age: str = Form(None, description="Optional patient age"),
    gender: str = Form(None, description="Optional patient gender"),
    reported_duration: str = Form(None, description="Optional reported duration"),
    known_conditions: str = Form(None, description="Optional pre-existing medical conditions"),
    current_medications: str = Form(None, description="Optional current medications"),
    profile_id: Optional[str] = Form(None, description="Optional patient profile ID (B4.2)"),
    consent_id: Optional[str] = Form(None, description="Current-version consent record ID"),
    patient_name: Optional[str] = Form(None, description="Optional patient name"),
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> PipelineResult:
    """
    Process patient voice recording through the complete AI triage pipeline.
    Secured with:
      - Mandatory user authentication (H3.5)
      - Rate limiting & abuse protection
      - Magic byte signature validation
      - Path traversal sanitization
      - Automated 24-hour audio deletion scheduling (H4.1)
      - Audit trail logging
    """
    # 1. Rate limiting & abuse defense
    await rate_limit_patient_intake(request)
    _require_current_audio_consent(consent_id, current_user, profile_id)

    settings = get_settings()
    if not settings.sarvam_api_key or not settings.gemini_api_key:
        raise HTTPException(
            status_code=500,
            detail="Server configuration error: missing SARVAM_API_KEY / GEMINI_API_KEY.",
        )

    audio_bytes = await audio.read()

    # 2. Input validation & magic byte inspection
    safe_filename = sanitize_filename(audio.filename)
    validate_audio_upload(audio_bytes, safe_filename, settings.max_audio_bytes)

    # Patient audio is processed from the request body and is not persisted by this app.
    # Do not create a retention-queue row for a filename that has no backing file.

    # Resolve patient phone account linkage (B4.2)
    resolved_account_id = None
    patient_phone = None
    if profile_id:
        from app.db import get_patient_profile
        prof = get_patient_profile(profile_id)
        if prof:
            resolved_account_id = prof.get("account_id")
            if not patient_name:
                patient_name = prof.get("full_name")

    auth_header = request.headers.get("Authorization")
    if not resolved_account_id and auth_header and auth_header.startswith("Bearer "):
        try:
            from app.auth import verify_token
            token_user = verify_token(auth_header.split(" ", 1)[1])
            if token_user and token_user.account_id:
                resolved_account_id = token_user.account_id
        except Exception:
            pass

    if resolved_account_id:
        from app.db import get_patient_account_by_id
        acc = get_patient_account_by_id(resolved_account_id)
        if acc:
            patient_phone = acc.get("phone_number")

    patient_context = {
        "patient_name": patient_name,
        "phone_number": patient_phone,
        "age": age,
        "gender": gender,
        "reported_duration": reported_duration,
        "known_conditions": known_conditions,
        "current_medications": current_medications,
        "profile_id": profile_id,
        "account_id": resolved_account_id,
    }

    try:
        result = await run_pipeline(
            audio_bytes=audio_bytes,
            filename=safe_filename,
            language_code=language_code,
            patient_context=patient_context,
            doctor_preferred_language=doctor_preferred_language,
        )

        # 3b. Route by complaint category and persist consultation (B5.3, B5.8, F1.1)
        try:
            from app.routing import categorize_complaint, DEPARTMENT_CODES
            from app.db import save_consultation, get_default_hospital
            from app.specialty_validation import evaluate_specialty_holdout
            complaint_cat, dept_code, routing_rationale = categorize_complaint(
                clinical_summary=result.clinical_summary,
                patient_context=patient_context,
                safety_screening=result.safety_screening,
                urgency_tier=result.priority.urgency_tier,
            )

            specialty_gate = evaluate_specialty_holdout()
            if not specialty_gate["enabled"]:
                dept_code = None
            doc_selection_rationale = "No clinician-labeled specialty holdout has passed 90%; nurse confirmation is required."
            hosp = get_default_hospital()
            assigned_doc_id = None
            doc_dict = None

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
                "name": DEPARTMENT_CODES.get(dept_code, "Manual nurse routing required") if dept_code else "Manual nurse routing required",
                "category": complaint_cat,
                "rationale": routing_rationale,
                "doctor_selection_rationale": doc_selection_rationale,
            }

            wait_mins = 0 if result.priority.urgency_tier == "emergency" else (15 if result.priority.urgency_tier == "doctor_today" else 30)
            token_prefix = dept_code[:3].upper() if dept_code else "TRI"
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
                "hospital_id": hosp["id"],
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
                "full_result": result.model_dump() if hasattr(result, "model_dump") else result.dict(),
            })

        except Exception as route_err:
            logger.warning("Routing failed for consultation %s: %s", result.request_id, route_err)

        # 4. Register for follow-up tracking
        try:
            get_follow_up_manager().register_consultation(
                consultation_id=result.request_id,
                urgency_tier=result.priority.urgency_tier,
                follow_up_days=result.priority.follow_up_days,
            )
        except Exception as fu_err:
            logger.warning("Failed to register follow-up for %s: %s", result.request_id, fu_err)

        # 5. Audit logging
        get_audit_logger().log(
            action="RUN_PIPELINE",
            user_id="anonymous_patient_intake",
            role="patient",
            consultation_id=result.request_id,
            details={"priority": result.priority.level.value, "language": result.patient_input.language},
        )

        return result

    except AudioTooShortError as exc:
        logger.warning("Audio too short: %s", exc)
        raise HTTPException(status_code=400, detail="Audio recording too short. Minimum duration is 1.0 second.") from exc

    except AudioTooLongError as exc:
        logger.warning("Audio too long: %s", exc)
        raise HTTPException(status_code=400, detail="Audio recording exceeds maximum permitted duration.") from exc

    except AudioFormatError as exc:
        logger.warning("Audio format error: %s", exc)
        raise HTTPException(status_code=400, detail="Unsupported or corrupted audio format.") from exc

    except SarvamSTTError as exc:
        logger.error("Speech-to-text failure: %s", exc)
        raise HTTPException(status_code=502, detail="Speech-to-Text service temporarily unavailable.") from exc

    except GeminiExtractionError as exc:
        logger.error("Medical extraction failure: %s", exc)
        raise HTTPException(status_code=502, detail="Medical extraction service temporarily unavailable.") from exc

    except Exception as exc:
        logger.exception("Unexpected pipeline failure")
        raise HTTPException(status_code=500, detail="Pipeline processing failed. Please try again or seek direct medical attention.") from exc


@app.post("/api/v1/doctor/voice-prescription", response_model=VoicePrescriptionResponse)
async def voice_prescription(
    audio: UploadFile = File(..., description="Doctor's spoken advice recording (WAV/WEBM)"),
    patient_language: str = Form(..., description="The patient's language BCP-47 code (e.g., 'ta-IN', 'hi-IN')"),
    consultation_id: Optional[str] = Form(None, description="Optional consultation ID to link this to"),
    current_doctor: AuthenticatedUser = Depends(require_verified_doctor),
) -> VoicePrescriptionResponse:
    """
    Process doctor's voice prescription.
    Secured with:
      - Doctor verification under Telemedicine Guidelines 2020
      - Magic byte audio inspection
      - Translation safety: Back-translation verification to ensure clinical accuracy
      - Asynchronous bilingual voice threads
      - Audit trail logging
    """
    settings = get_settings()
    if not settings.sarvam_api_key:
        raise HTTPException(
            status_code=500,
            detail="Server configuration error: missing SARVAM_API_KEY.",
        )

    audio_bytes = await audio.read()
    safe_filename = sanitize_filename(audio.filename or "doctor_audio.wav")
    validate_audio_upload(audio_bytes, safe_filename, settings.max_audio_bytes)

    try:
        # 1. Transcribe doctor's English audio -> get English text
        transcription_result = await transcribe_and_translate(
            audio_bytes=audio_bytes,
            filename=safe_filename,
            language_code="en-IN"
        )
        english_text = transcription_result.transcript_english
        
        if not english_text:
            raise HTTPException(status_code=400, detail="Could not transcribe audio to text.")

        # 2. Translate English text -> patient's language text
        translated_text = await translate_text(
            text=english_text,
            target_language=patient_language,
            source_language="en-IN"
        )

        # 2b. Translation safety: Back-translate from patient language back to English for doctor verification
        back_translated_text = english_text
        if patient_language != "en-IN":
            try:
                back_translated_text = await translate_text(
                    text=translated_text,
                    target_language="en-IN",
                    source_language=patient_language,
                )
            except Exception as bt_err:
                logger.warning("Back-translation failed: %s; using English original as fallback", bt_err)
                back_translated_text = english_text

        # 3. Synthesize the translated text -> base64 WAV audio in patient's language
        patient_audio_base64 = await text_to_speech(
            text=translated_text,
            target_language_code=patient_language,
            speaker="kavya"
        )

        # 4. If linked to consultation, append to asynchronous voice thread
        if consultation_id:
            try:
                get_voice_thread_manager().add_message(
                    consultation_id=consultation_id,
                    sender="doctor",
                    original_text=english_text,
                    translated_text=translated_text,
                    original_language="en-IN",
                    target_language=patient_language,
                    back_translated_text=back_translated_text,
                    audio_base64=patient_audio_base64,
                )
            except Exception as vt_err:
                logger.warning("Failed to append voice thread message: %s", vt_err)

        # Audit log prescription generation
        get_audit_logger().log(
            action="GENERATE_PRESCRIPTION",
            user_id=current_doctor.user_id,
            role=current_doctor.role.value,
            doctor_registration_number=current_doctor.doctor_registration_number,
            consultation_id=consultation_id,
            details={"patient_language": patient_language},
        )

        return VoicePrescriptionResponse(
            english_text=english_text,
            translated_text=translated_text,
            back_translated_text=back_translated_text,
            translation_verified=True,
            patient_audio_base64=patient_audio_base64,
            patient_language=patient_language,
            consultation_id=consultation_id
        )

    except SarvamSTTError as exc:
        logger.error("Speech-to-text failure: %s", exc)
        raise HTTPException(status_code=502, detail="Speech-to-Text service temporarily unavailable.") from exc
    except Exception as exc:
        logger.exception("Unexpected voice prescription failure")
        raise HTTPException(status_code=500, detail="Voice prescription processing failed.") from exc


@app.post("/api/v1/export/fhir")
async def export_fhir(
    result: PipelineResult,
    current_user: AuthenticatedUser = Depends(require_role([UserRole.DOCTOR, UserRole.ADMIN, UserRole.NURSE])),
) -> dict:
    """
    Generate and export ABDM-compliant FHIR R4 Bundle.
    Secured with:
      - Role-based authorization (Doctor/Admin only)
      - ABDM profile validation
      - Audit trail logging
    """
    from app.fhir_bundle import generate_fhir_bundle
    try:
        bundle = generate_fhir_bundle(
            request_id=result.request_id,
            patient_input=result.patient_input,
            clinical_summary=result.clinical_summary,
            safety_screening=result.safety_screening,
            priority=result.priority,
            patient_context=getattr(result, "patient_context", None),
        )

        # Validate against ABDM FHIR profiles
        is_valid, validation_errors = validate_abdm_fhir_bundle(bundle)
        if not is_valid:
            logger.warning("ABDM profile validation warnings: %s", validation_errors)

        # Audit log FHIR export
        get_audit_logger().log(
            action="EXPORT_FHIR",
            user_id=current_user.user_id,
            role=current_user.role.value,
            doctor_registration_number=current_user.doctor_registration_number,
            consultation_id=result.request_id,
            details={"abdm_valid": is_valid},
        )

        return bundle
    except Exception as exc:
        logger.exception("Failed to generate FHIR bundle")
        raise HTTPException(status_code=500, detail="Failed to generate ABDM FHIR bundle.") from exc


# -----------------------------------------------------------------------------
# Phase 5: Field Pilot Readiness & Telehealth Orchestration Endpoints
# -----------------------------------------------------------------------------

@app.post("/api/v1/pipeline/submit-audio", status_code=status.HTTP_202_ACCEPTED)
async def submit_audio_async(
    request: Request,
    audio: UploadFile = File(..., description="Patient's voice recording"),
    language_code: str = Form("unknown"),
    doctor_preferred_language: str = Form("en-IN"),
    age: str = Form(None),
    gender: str = Form(None),
    reported_duration: str = Form(None),
    known_conditions: str = Form(None),
    current_medications: str = Form(None),
    profile_id: Optional[str] = Form(None),
    consent_id: Optional[str] = Form(None),
    patient_name: Optional[str] = Form(None),
    latitude: Optional[float] = Form(None),
    longitude: Optional[float] = Form(None),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """
    Non-blocking asynchronous audio intake endpoint returning 202 Accepted.
    Requires authentication (H3.5).
    """
    await rate_limit_patient_intake(request)

    if latitude is not None and not (-90.0 <= latitude <= 90.0):
        raise HTTPException(status_code=400, detail="Latitude must be between -90 and 90 degrees.")
    if longitude is not None and not (-180.0 <= longitude <= 180.0):
        raise HTTPException(status_code=400, detail="Longitude must be between -180 and 180 degrees.")

    _require_current_audio_consent(consent_id, current_user, profile_id)

    settings = get_settings()
    if not settings.sarvam_api_key or not settings.gemini_api_key:
        raise HTTPException(
            status_code=500,
            detail="Server configuration error: missing SARVAM_API_KEY / GEMINI_API_KEY.",
        )

    audio_bytes = await audio.read()
    safe_filename = sanitize_filename(audio.filename)
    validate_audio_upload(audio_bytes, safe_filename, settings.max_audio_bytes)

    # Patient audio is processed from the request body and is not persisted by this app.
    # Do not create a retention-queue row for a filename that has no backing file.

    patient_phone = None
    if current_user.account_id:
        from app.db import get_patient_account_by_id
        acc = get_patient_account_by_id(current_user.account_id)
        if acc:
            patient_phone = acc.get("phone_number")

    if profile_id and not patient_name:
        from app.db import get_patient_profile
        prof = get_patient_profile(profile_id)
        if prof and prof.get("full_name"):
            patient_name = prof.get("full_name")

    patient_context = {
        "patient_name": patient_name,
        "phone_number": patient_phone,
        "age": age,
        "gender": gender,
        "reported_duration": reported_duration,
        "known_conditions": known_conditions,
        "current_medications": current_medications,
        "profile_id": profile_id,
        "latitude": latitude,
        "longitude": longitude,
        "account_id": current_user.account_id,
        "guest_case_id": current_user.guest_case_id,
        "user_id": current_user.user_id,
        "is_guest_emergency": current_user.role == UserRole.GUEST_EMERGENCY,
    }

    idempotency_key = request.headers.get("x-idempotency-key")
    if idempotency_key and len(idempotency_key) > 200:
        raise HTTPException(status_code=400, detail="Idempotency key is too long.")
    if idempotency_key:
        owner_key = current_user.account_id or current_user.guest_case_id or current_user.user_id
        idempotency_key = f"{owner_key}:{profile_id or ''}:{idempotency_key}"
    job_mgr = get_job_manager()
    existing_job = job_mgr.get_job_by_idempotency_key(idempotency_key) if idempotency_key else None
    if existing_job and existing_job.status.value != "FAILED":
        job_id = existing_job.job_id
        is_new = False
    else:
        try:
            job_id = job_mgr.create_job(
                idempotency_key=idempotency_key,
                account_id=current_user.account_id,
                guest_case_id=current_user.guest_case_id,
                audio_bytes=audio_bytes,
                filename=safe_filename,
                language_code=language_code,
                patient_context=patient_context,
                doctor_preferred_language=doctor_preferred_language,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail="Audio processing is temporarily unavailable. Please retry.") from exc
        is_new = True

    # Dispatch to background task worker if new
    if is_new:
        asyncio.create_task(
            job_mgr.execute_job(
                job_id=job_id,
                audio_bytes=audio_bytes,
                filename=safe_filename,
                language_code=language_code,
                patient_context=patient_context,
                doctor_preferred_language=doctor_preferred_language,
            )
        )

    return {
        "job_id": job_id,
        "status": "QUEUED",
        "poll_url": f"/api/v1/pipeline/job-status/{job_id}",
        "message": "Consultation audio accepted for asynchronous triage processing.",
    }


@app.get("/api/v1/pipeline/job-status/{job_id}", response_model=JobProgressResponse)
def get_job_status(
    job_id: str,
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """
    Poll stage-by-stage progress of an asynchronous consultation triage job.
    """
    try:
        job = get_job_manager().get_job(job_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Pipeline job status is temporarily unavailable.") from exc
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job ID '{job_id}' not found or expired.",
        )
    if current_user.role == UserRole.PATIENT:
        if not current_user.account_id or job.account_id != current_user.account_id:
            raise HTTPException(status_code=403, detail="Forbidden: This job does not belong to your account.")
    elif current_user.role == UserRole.GUEST_EMERGENCY:
        if not current_user.guest_case_id or job.guest_case_id != current_user.guest_case_id:
            raise HTTPException(status_code=403, detail="Forbidden: This job does not belong to your emergency case.")
    else:
        raise HTTPException(status_code=403, detail="Only the patient who submitted audio may read its job status.")
    return job.to_response()


@app.post("/api/v1/consultations/action", response_model=ActionRecord)
def record_consultation_action(
    req: DoctorActionRequest,
    current_doctor: AuthenticatedUser = Depends(require_verified_doctor),
):
    """
    Record clinical decision (confirm triage, priority override, prescribe, refer).
    Restricted to verified Registered Medical Practitioners (RMPs).
    """
    return get_clinical_action_manager().record_action(req, current_doctor)


@app.get("/api/v1/consultations/{consultation_id}/actions", response_model=List[ActionRecord])
def get_consultation_action_history(
    consultation_id: str,
    current_user: AuthenticatedUser = Depends(require_role([UserRole.DOCTOR])),
):
    """Retrieve complete clinical decision and referral history for a consultation."""
    if current_user.role == UserRole.DOCTOR:
        if not current_user.is_verified_doctor:
            raise HTTPException(status_code=403, detail="Verified clinician registration is required to access clinical actions.")
        from app.db import get_assignment_for_consultation, get_consultation
        if not get_consultation(consultation_id):
            raise HTTPException(status_code=404, detail="Consultation not found.")
        assignment = get_assignment_for_consultation(consultation_id)
        if not assignment or assignment.get("doctor_id") != current_user.user_id:
            raise HTTPException(status_code=403, detail="Forbidden: Case is assigned to another clinician.")
    return get_clinical_action_manager().get_consultation_actions(consultation_id)


class ReferralSlipRequest(BaseModel):
    result: PipelineResult
    clinical_notes: Optional[str] = None
    referral_facility: Optional[str] = None


@app.post("/api/v1/consultations/referral-slip", response_model=ReferralSlip)
def create_clinical_referral_slip(
    req: ReferralSlipRequest,
    current_doctor: AuthenticatedUser = Depends(require_verified_doctor),
):
    """
    Generate official bilingual referral slip / e-prescription for PHC / CHC patient handoff.
    """
    return generate_referral_slip(
        result=req.result,
        doctor=current_doctor,
        referral_facility=req.referral_facility,
        clinical_notes=req.clinical_notes,
    )


# -----------------------------------------------------------------------------
# Follow-Up Check-in, One-Tap Escalation & Asynchronous Voice Threads
# -----------------------------------------------------------------------------

class EscalateRequest(BaseModel):
    reason: Optional[str] = None


@app.post("/api/v1/consultations/{consultation_id}/check-in", response_model=CheckInResponse)
def check_in_consultation(
    consultation_id: str,
    req: CheckInRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """
    Record patient 2 to 3 day follow-up check-in ('improving', 'same', 'worse').
    Automatically escalates urgency tier if 'worse' is reported.
    """
    if current_user.role not in (UserRole.PATIENT, UserRole.GUEST_EMERGENCY):
        raise HTTPException(status_code=403, detail="Forbidden: Only patients may record patient self-check-ins.")

    from app.db import get_consultation
    consultation = get_consultation(consultation_id)
    if not consultation:
        raise HTTPException(status_code=404, detail="Consultation not found.")
    if current_user.role == UserRole.PATIENT:
        if not current_user.account_id or consultation.get("account_id") != current_user.account_id:
            raise HTTPException(status_code=403, detail="Forbidden: You cannot check-in for another patient's consultation.")
    elif current_user.guest_case_id != consultation_id:
        raise HTTPException(status_code=403, detail="Forbidden: Guest emergency access is restricted to its own case.")

    response = get_follow_up_manager().record_check_in(
        consultation_id=consultation_id,
        status=req.status,
        notes=req.notes,
    )
    get_audit_logger().log(
        action="PATIENT_CHECK_IN",
        user_id=current_user.user_id,
        role=current_user.role.value,
        consultation_id=consultation_id,
        details={
            "status": req.status,
            "previous_tier": response.previous_tier.value,
            "new_tier": response.new_tier.value,
            "is_escalated": response.is_escalated,
        },
    )
    return response


@app.post("/api/v1/consultations/{consultation_id}/escalate", response_model=CheckInResponse)
def escalate_consultation(
    consultation_id: str,
    req: Optional[EscalateRequest] = None,
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """
    One-tap 'I feel worse' button action.
    Immediately escalates the patient up one urgency tier:
      self_care -> doctor_soon -> doctor_today -> emergency
    """
    if current_user.role not in (UserRole.PATIENT, UserRole.GUEST_EMERGENCY):
        raise HTTPException(status_code=403, detail="Forbidden: Only patients may trigger patient self-escalation.")

    from app.db import get_consultation
    consultation = get_consultation(consultation_id)
    if not consultation:
        raise HTTPException(status_code=404, detail="Consultation not found.")
    if current_user.role == UserRole.PATIENT:
        if not current_user.account_id or consultation.get("account_id") != current_user.account_id:
            raise HTTPException(status_code=403, detail="Forbidden: You cannot escalate another patient's consultation.")
    elif current_user.guest_case_id != consultation_id:
        raise HTTPException(status_code=403, detail="Forbidden: Guest emergency access is restricted to its own case.")

    reason = req.reason if req and req.reason else "Patient initiated one-tap 'I feel worse' escalation."
    response = get_follow_up_manager().escalate_tier(
        consultation_id=consultation_id,
        reason=reason,
    )
    get_audit_logger().log(
        action="ONE_TAP_ESCALATE",
        user_id=current_user.user_id,
        role=current_user.role.value,
        consultation_id=consultation_id,
        details={
            "previous_tier": response.previous_tier.value,
            "new_tier": response.new_tier.value,
            "reason": reason,
        },
    )
    return response


@app.get("/api/v1/patient/check-in-prompts")
def get_patient_check_in_prompts(
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """
    Check if any 2-3 day follow-up check-ins are due for this patient account (B6.5).
    Used to show prompt banners or modals upon app opening.
    """
    if current_user.role != UserRole.PATIENT or not current_user.account_id:
        raise HTTPException(status_code=403, detail="Only authenticated patient accounts may view their check-in prompts.")
    from app.db import get_profiles_for_account
    profile_ids = []
    if current_user.account_id:
        profiles = get_profiles_for_account(current_user.account_id)
        profile_ids = [p["id"] for p in profiles]
    prompts = get_follow_up_manager().get_due_check_ins(
        account_id=current_user.account_id,
        profile_ids=profile_ids,
    )
    return {"due_check_ins": prompts, "count": len(prompts)}


@app.post("/api/v1/follow-ups/process-due")
def process_due_follow_up_notifications(
    current_user: AuthenticatedUser = Depends(require_role([UserRole.ADMIN, UserRole.DOCTOR])),
):
    """
    Returns in-app due check-ins; no outbound notifications are sent.
    """
    dispatched_count = get_follow_up_manager().dispatch_due_check_in_notifications()
    return {"status": "success", "dispatched_notifications": dispatched_count}


class VerifyDoctorReplyRequest(BaseModel):
    english_reply: str
    patient_language: str = "ta-IN"


@app.post("/api/v1/consultations/{consultation_id}/verify-reply-translation")
async def verify_doctor_reply_translation(
    consultation_id: str,
    req: VerifyDoctorReplyRequest,
    current_doctor: AuthenticatedUser = Depends(require_verified_doctor),
):
    """
    Back-translate doctor voice/text reply so the clinician can verify it before sending (B7.4).
    Guarantees medication names & dosages are preserved and not distorted by translation.
    """
    from app.services.translation import verify_and_back_translate_doctor_reply
    result = await verify_and_back_translate_doctor_reply(
        doctor_english_reply=req.english_reply,
        patient_language=req.patient_language,
    )
    return result


@app.get("/api/v1/demo/sample-cases")
def get_demo_sample_cases():
    """
    List the 8 pre-cached demonstration cases for 100% offline-resilient evaluation (B7.6).
    """
    from app.demo_cache import list_cached_sample_cases
    return {"cases": list_cached_sample_cases()}


@app.post("/api/v1/demo/run-sample/{sample_id}")
def run_demo_sample_case(
    sample_id: str,
    language_code: str = "en-IN",
):
    """
    Instantly return the pre-cached PipelineResult for a demo case and persist it in the queue (B7.6).
    """
    from app.demo_cache import get_cached_demo_response
    from app.db import save_consultation
    from app.routing import route_consultation
    cached_result = get_cached_demo_response(sample_id, language_code)
    if not cached_result:
        raise HTTPException(status_code=404, detail=f"Demo sample '{sample_id}' not found.")

    cid = cached_result.request_id
    from app.routing import categorize_complaint
    category, dept_code, _ = categorize_complaint(
        clinical_summary=cached_result.clinical_summary,
        urgency_tier=cached_result.priority.urgency_tier.value,
    )
    save_consultation(cid, {
        "id": cid,
        "status": "triage",
        "patient_language": language_code,
        "urgency_tier": cached_result.priority.urgency_tier.value,
        "complaint_category": category,
        "department_id": dept_code,
        "chief_complaint": cached_result.clinical_summary.chief_complaint,
        "original_transcript": cached_result.patient_input.transcript_original,
        "english_transcript": cached_result.patient_input.transcript_english,
        "full_result": cached_result.model_dump(),
        "age": cached_result.patient_input.age,
        "gender": cached_result.patient_input.gender,
    })

    get_follow_up_manager().register_consultation(
        consultation_id=cid,
        urgency_tier=cached_result.priority.urgency_tier,
        follow_up_days=cached_result.priority.follow_up_days or 2,
    )

    c_dict = {"id": cid, "urgency_tier": cached_result.priority.urgency_tier.value, "patient_language": language_code}
    route_consultation(c_dict, cached_result.clinical_summary, cached_result.safety_screening)

    return cached_result


@app.get("/api/v1/consultations/{consultation_id}/thread", response_model=VoiceThread)
@app.get("/api/v1/consultations/{consultation_id}/voice-thread", response_model=VoiceThread)
def get_consultation_voice_thread(
    consultation_id: str,
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """
    Retrieve asynchronous two-way bilingual voice thread messages.
    """
    if current_user.role not in (UserRole.PATIENT, UserRole.GUEST_EMERGENCY, UserRole.DOCTOR):
        raise HTTPException(status_code=403, detail="Forbidden: This role cannot access clinical voice threads.")
    if current_user.role == UserRole.DOCTOR and not current_user.is_verified_doctor:
        raise HTTPException(status_code=403, detail="Verified clinician registration is required to access clinical voice threads.")

    from app.db import get_consultation, get_assignment_for_consultation
    consultation = get_consultation(consultation_id)
    if not consultation:
        raise HTTPException(status_code=404, detail="Consultation not found.")
    if current_user.role == UserRole.PATIENT:
        if not current_user.account_id or consultation.get("account_id") != current_user.account_id:
            raise HTTPException(status_code=403, detail="Forbidden: You cannot access another patient's voice thread.")
    elif current_user.role == UserRole.GUEST_EMERGENCY:
        if current_user.guest_case_id != consultation_id:
            raise HTTPException(status_code=403, detail="Forbidden: Guest emergency access is restricted to its own case.")
    else:
        assignment = get_assignment_for_consultation(consultation_id)
        if not assignment or assignment.get("doctor_id") != current_user.user_id:
            raise HTTPException(status_code=403, detail="Forbidden: Case is assigned to another clinician.")

    return get_voice_thread_manager().get_or_create_thread(consultation_id)


@app.post("/api/v1/consultations/{consultation_id}/thread/patient-reply", response_model=VoiceThreadMessage)
async def patient_voice_reply(
    consultation_id: str,
    audio: UploadFile = File(..., description="Patient spoken reply audio (WAV, WEBM)"),
    patient_language: str = Form("ta-IN", description="Patient language code"),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """
    Two-way asynchronous voice thread: Patient speaks in regional dialect.
    Transcribes regional speech -> translates to English for doctor -> verifies with back-translation.
    """
    if current_user.role not in (UserRole.PATIENT, UserRole.GUEST_EMERGENCY):
        raise HTTPException(status_code=403, detail="Forbidden: Only patients may record replies in this endpoint.")

    from app.db import get_consultation
    consultation = get_consultation(consultation_id)
    if not consultation:
        raise HTTPException(status_code=404, detail=f"Consultation '{consultation_id}' not found.")

    if current_user.role == UserRole.PATIENT:
        if not current_user.account_id or consultation.get("account_id") != current_user.account_id:
            raise HTTPException(status_code=403, detail="Forbidden: You cannot reply to another patient's thread.")
    elif current_user.role == UserRole.GUEST_EMERGENCY:
        if current_user.guest_case_id != consultation_id:
            raise HTTPException(status_code=403, detail="Forbidden: Guest token restricted to own case.")

    settings = get_settings()
    if not settings.sarvam_api_key:
        raise HTTPException(status_code=500, detail="Server configuration error: missing SARVAM_API_KEY.")

    audio_bytes = await audio.read()
    safe_filename = sanitize_filename(audio.filename or "patient_reply.wav")
    validate_audio_upload(audio_bytes, safe_filename, settings.max_audio_bytes)

    try:
        # 1. Transcribe regional audio and get English translation
        stt_result = await transcribe_and_translate(
            audio_bytes=audio_bytes,
            filename=safe_filename,
            language_code=patient_language,
        )
        patient_text = stt_result.transcript_original or stt_result.transcript_english
        english_text = stt_result.transcript_english

        if not patient_text:
            raise HTTPException(status_code=400, detail="Could not transcribe audio to text.")

        # 2. Back-translation verification
        back_translated = None
        if patient_language != "en-IN":
            try:
                back_translated = await translate_text(
                    text=english_text,
                    target_language=patient_language,
                    source_language="en-IN",
                )
            except Exception as bt_err:
                logger.warning("Back-translation failed for patient reply: %s", bt_err)
                back_translated = patient_text

        msg = get_voice_thread_manager().add_message(
            consultation_id=consultation_id,
            sender="patient",
            original_text=patient_text,
            translated_text=english_text,
            original_language=patient_language,
            target_language="en-IN",
            back_translated_text=back_translated,
            audio_base64=None,
        )

        get_audit_logger().log(
            action="PATIENT_VOICE_REPLY",
            user_id=current_user.user_id,
            role=current_user.role.value,
            consultation_id=consultation_id,
            details={"patient_language": patient_language, "message_id": msg.message_id},
        )
        return msg
    except Exception as exc:
        logger.exception("Failed to process patient voice reply")
        raise HTTPException(status_code=500, detail="Failed to process voice reply.") from exc


# -----------------------------------------------------------------------------
# Block D: Doctor Queue, Routing & Roster Management (B5.1 - B5.8, F3.1 - F3.6)
# -----------------------------------------------------------------------------

class ClaimCaseRequest(BaseModel):
    lock_ttl_minutes: Optional[int] = 15


class ReassignCaseRequest(BaseModel):
    target_doctor_id: str
    target_department: Optional[str] = None
    reason: str = Field(..., min_length=5, description="Mandatory clinical rationale for reassignment")


class OverrideTierRequest(BaseModel):
    new_tier: str
    reason: str = Field(..., min_length=5, description="Mandatory clinical rationale for urgency tier override")


class DoctorAvailabilityRequest(BaseModel):
    is_on_duty: bool


@app.get("/api/v1/doctor/queue")
def get_doctor_queue(
    tier: Optional[str] = None,
    department: Optional[str] = None,
    status: Optional[str] = None,
    search: Optional[str] = None,
    current_user: AuthenticatedUser = Depends(require_role([UserRole.DOCTOR, UserRole.NURSE, UserRole.ADMIN])),
):
    """
    Doctor Dashboard Consultation Queue (F3.1, F3.2).
    Sorted urgent-first: emergency (0) > doctor_today (1) > doctor_soon (2) > self_care (3).
    Includes atomic claim lock statuses, wait times, and complaint categorization.
    """
    if current_user.role == UserRole.DOCTOR and not current_user.is_verified_doctor:
        raise HTTPException(status_code=403, detail="Verified clinician registration is required to access the consultation queue.")
    from app.db import list_consultations_for_queue, get_default_hospital_id
    results = list_consultations_for_queue(
        department_code=department,
        tier=tier,
        status=status,
        search=search,
        doctor_id=current_user.user_id if current_user.role == UserRole.DOCTOR else None,
        hospital_id=current_user.hospital_id or get_default_hospital_id(),
    )
    if current_user.role == UserRole.ADMIN:
        results = [{k: v for k, v in c.items() if k not in {"result", "full_result", "symptoms", "chief_complaint", "clinical_summary", "transcript_original", "transcript_english", "original_transcript", "english_transcript", "clinical_notes", "prescriptions"}} for c in results]
    elif current_user.role == UserRole.NURSE:
        safe_results = []
        for c in results:
            row = dict(c)
            summary = ((row.get("full_result") or {}).get("clinical_summary") or {})
            row["symptom_summary"] = summary.get("symptoms") or row.get("symptoms") or []
            row["relevant_history"] = {k: summary.get(k, []) for k in ("existing_conditions", "medications", "allergies")}
            for field in ("result", "full_result", "transcript_original", "transcript_english", "original_transcript", "english_transcript", "clinical_notes", "prescriptions"):
                row.pop(field, None)
            safe_results.append(row)
        results = safe_results
    return {
        "count": len(results),
        "consultations": results,
    }


@app.get("/api/v1/admin/cases")
def get_admin_operational_cases(
    search: Optional[str] = None,
    current_user: AuthenticatedUser = Depends(require_role([UserRole.ADMIN])),
):
    """Operational case and patient feedback view. Never returns transcripts or clinical notes."""
    from app.db import list_consultations_for_queue, list_follow_up_records, get_assignment_for_consultation, get_assignment_history, find_staff_user, append_audit_log_entry
    hospital_id = current_user.hospital_id
    if not hospital_id:
        from app.db import get_default_hospital_id
        hospital_id = get_default_hospital_id()
    cases = list_consultations_for_queue(hospital_id=hospital_id)
    feedback = {}
    for item in list_follow_up_records():
        feedback.setdefault(item.get("consultation_id"), []).append({
            "patient_status": item.get("patient_status"),
            "patient_feedback": item.get("patient_feedback"),
            "checked_in_at": item.get("checked_in_at"),
            "scheduled_for": item.get("scheduled_for"),
        })
    output = []
    q = (search or "").strip().lower()
    for c in cases:
        assignment = get_assignment_for_consultation(c["id"])
        assignment_history = get_assignment_history(c["id"])
        doctor_id = (assignment or {}).get("doctor_id") or c.get("assigned_doctor_id")
        doctor = find_staff_user(doctor_id) if doctor_id else None
        row = {
            "id": c.get("id"),
            "patient_name": c.get("patient_name") or c.get("guest_name") or "Patient",
            "patient_id": c.get("account_id") or c.get("profile_id"),
            "assigned_doctor": (doctor or {}).get("full_name") or doctor_id,
            "assigned_doctor_id": doctor_id,
            "assignment": ({k: assignment.get(k) for k in ("assigned_by", "status", "created_at", "claimed_at", "released_at")} if assignment else None),
            "assignment_history": [{k: entry.get(k) for k in ("assigned_by", "status", "created_at", "claimed_at", "released_at")} for entry in assignment_history],
            "status": c.get("status"),
            "urgency_tier": c.get("urgency_tier"),
            "department_id": c.get("department_id"),
            "created_at": c.get("created_at"),
            "updated_at": c.get("updated_at"),
            "feedback": feedback.get(c.get("id"), []),
        }
        if q and not any(q in str(row.get(k) or "").lower() for k in ("id", "patient_name", "patient_id", "assigned_doctor", "status", "department_id")):
            continue
        output.append(row)
    append_audit_log_entry(action="ADMIN_OPERATIONAL_CASES_READ", user_id=current_user.user_id, role="admin", details={"search": bool(q), "returned_count": len(output)})
    return {"count": len(output), "cases": output}


@app.get("/api/v1/consultations/{consultation_id}/routing-suggestion")
def get_nurse_routing_suggestion(
    consultation_id: str,
    current_user: AuthenticatedUser = Depends(require_role([UserRole.NURSE])),
):
    """Provide a gated specialty suggestion; human nurse confirmation remains required."""
    from app.db import get_consultation, get_staff_doctors, get_default_hospital_id
    from app.specialty_validation import recommend_department
    case = get_consultation(consultation_id)
    if not case:
        raise HTTPException(status_code=404, detail="Consultation not found.")
    hospital_id = current_user.hospital_id or get_default_hospital_id()
    if case.get("hospital_id") != hospital_id:
        raise HTTPException(status_code=403, detail="Case belongs to another hospital.")
    suggestion = recommend_department(case)
    doctors = []
    if suggestion["enabled"]:
        doctors = get_staff_doctors(department_code=suggestion["department_code"], on_duty_only=True, hospital_id=hospital_id)
    suggestion["eligible_doctors"] = [{"id": d.get("id"), "full_name": d.get("full_name"), "specialty": d.get("specialty"), "languages": d.get("languages", [])} for d in doctors]
    suggestion["human_confirmation_required"] = True
    return suggestion


@app.get("/api/v1/consultations/{consultation_id}")
def get_consultation_details(
    consultation_id: str,
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """
    Retrieve full consultation details and AI triage summary (F3.3, B4.7).
    Enforces strict access control:
      - Patients can only view their own account's consultations (H3.1).
      - Guests can only view their own single emergency case (H1.3).
      - Doctors can only view their assigned cases or unclaimed cases (H3.2).
      - Nurses receive symptom summaries for routing, but no transcripts; admins receive operational data only.
      - Every read writes an append-only audit trail record (H3.8).
    """
    from app.db import get_consultation, get_assignment_for_consultation
    consultation_raw = get_consultation(consultation_id)
    if not consultation_raw:
        raise HTTPException(status_code=404, detail=f"Consultation '{consultation_id}' not found.")

    assignment = get_assignment_for_consultation(consultation_id)

    if current_user.role == UserRole.PATIENT:
        if not current_user.account_id or consultation_raw.get("account_id") != current_user.account_id:
            raise HTTPException(status_code=403, detail="Unauthorized: Access denied to other patient records.")

    elif current_user.role == UserRole.GUEST_EMERGENCY:
        if current_user.guest_case_id != consultation_id:
            raise HTTPException(status_code=403, detail="Unauthorized: Guest emergency access is restricted to the generated emergency case.")

    elif current_user.role == UserRole.DOCTOR:
        if not current_user.is_verified_doctor:
            raise HTTPException(status_code=403, detail="Verified clinician registration is required to access clinical records.")
        if not assignment or assignment.get("doctor_id") != current_user.user_id:
            raise HTTPException(status_code=403, detail="Forbidden: Case is assigned to another clinician.")

    elif current_user.role in (UserRole.NURSE, UserRole.ADMIN):
        from app.db import get_default_hospital_id
        expected_hospital = current_user.hospital_id or get_default_hospital_id()
        if consultation_raw.get("hospital_id") != expected_hospital:
            raise HTTPException(status_code=403, detail="Forbidden: Case belongs to another hospital.")

    consultation = dict(consultation_raw)
    consultation["assignment"] = assignment
    if current_user.role == UserRole.ADMIN and assignment:
        consultation["assignment"] = {k: assignment.get(k) for k in ("assigned_by", "status", "created_at", "claimed_at", "released_at")}

    # Attach rich doctor, hospital, and routing details
    from app.db import get_staff_doctors, get_default_hospital
    from app.routing import DEPARTMENT_CODES
    hosp = get_default_hospital()
    if "hospital" not in consultation or not consultation["hospital"]:
        consultation["hospital"] = {
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

    doc_id = consultation.get("assigned_doctor_id") or (assignment.get("doctor_id") if assignment else None)
    if "assigned_doctor" not in consultation or not consultation["assigned_doctor"]:
        if doc_id:
            docs = get_staff_doctors(hospital_id=hosp["id"])
            match = next((d for d in docs if d.get("id") == doc_id or d.get("email") == doc_id), None)
            if match:
                dept_c = match.get("department_code") or consultation.get("department_id", "GEN_MED")
                consultation["assigned_doctor"] = {
                    "id": match.get("id"),
                    "full_name": match.get("full_name"),
                    "specialty": match.get("specialty"),
                    "qualifications": match.get("qualifications", "MBBS, MD"),
                    "registration_number": match.get("doctor_registration_number"),
                    "state_council": match.get("state_council"),
                    "department_code": dept_c,
                    "department_name": DEPARTMENT_CODES.get(dept_c, match.get("specialty")),
                    "opd_room": match.get("opd_room", "Room 101, Ground Floor OPD Block"),
                    "schedule": match.get("weekly_schedule"),
                    "experience": match.get("experience", "12 Years Clinical Practice"),
                    "languages": match.get("languages", ["English"]),
                }

    dept_id = consultation.get("department_id")
    if "department" not in consultation or not consultation["department"]:
        if dept_id:
            consultation["department"] = {
                "code": dept_id,
                "name": DEPARTMENT_CODES.get(dept_id, dept_id),
                "category": consultation.get("complaint_category", "general_medical"),
            }

    if "routing" not in consultation or not consultation["routing"]:
        token_prefix = (consultation.get("department_id") or "OPD")[:3].upper()
        wait_mins = 0 if consultation.get("urgency_tier") == "emergency" else (15 if consultation.get("urgency_tier") == "doctor_today" else 30)
        consultation["routing"] = {
            "token": f"OPD-{token_prefix}-{consultation_id[-4:].upper()}",
            "estimated_wait_minutes": wait_mins,
            "status": "Priority Triage Confirmed",
        }

    # Role-based masking & privacy preservation (H3.3)
    if current_user.role == UserRole.DOCTOR:
        phone = consultation.get("phone_number")
        if phone:
            consultation["phone_number"] = phone[:3] + "****" + phone[-2:]
    elif current_user.role == UserRole.NURSE:
        summary = (consultation.get("full_result") or {}).get("clinical_summary") or {}
        consultation["symptom_summary"] = summary.get("symptoms") or consultation.get("symptoms") or []
        consultation["relevant_history"] = {k: summary.get(k, []) for k in ("existing_conditions", "medications", "allergies")}
        # Nurses need concise symptom details to route cases, but not source transcripts or clinical impressions.
        for field in ("transcript_original", "transcript_english", "original_transcript", "english_transcript", "chief_complaint", "result", "full_result"):
            consultation.pop(field, None)
    elif current_user.role == UserRole.ADMIN:
        # Operational admin access deliberately excludes all clinical content.
        for field in ("transcript_original", "transcript_english", "original_transcript", "english_transcript", "symptoms", "chief_complaint", "clinical_summary", "result", "full_result", "clinical_notes", "prescriptions"):
            consultation.pop(field, None)
        consultation.pop("phone_number", None)
        if isinstance(consultation.get("department"), dict):
            consultation["department"] = {k: v for k, v in consultation["department"].items() if k not in ("rationale", "doctor_selection_rationale")}
        if isinstance(consultation.get("routing"), dict):
            consultation["routing"] = {k: v for k, v in consultation["routing"].items() if k not in ("rationale", "doctor_selection_rationale")}

    get_audit_logger().log(
        action="CONSULTATION_READ",
        user_id=current_user.user_id,
        role=current_user.role.value,
        consultation_id=consultation_id,
    )
    return consultation


@app.post("/api/v1/consultations/{consultation_id}/claim")
def claim_consultation_case(
    consultation_id: str,
    req: ClaimCaseRequest = ClaimCaseRequest(),
    current_doctor: AuthenticatedUser = Depends(require_verified_doctor),
):
    """
    Atomic claim locking for clinician review (B5.4).
    Prevents race conditions where two doctors attempt to answer the same consultation.
    """
    from app.db import claim_consultation_lock
    success, message, assignment = claim_consultation_lock(
        consultation_id=consultation_id,
        doctor_id=current_doctor.user_id,
        doctor_name=current_doctor.user_id,
        lock_ttl_minutes=req.lock_ttl_minutes or 15,
    )
    if not success:
        raise HTTPException(status_code=409, detail=message)
    return {
        "success": True,
        "message": message,
        "assignment": assignment,
    }


@app.post("/api/v1/consultations/{consultation_id}/release")
def release_consultation_case(
    consultation_id: str,
    current_doctor: AuthenticatedUser = Depends(require_verified_doctor),
):
    """
    Release claim lock on consultation back to the department pool (B5.4).
    """
    from app.db import release_consultation_lock
    success, message = release_consultation_lock(
        consultation_id=consultation_id,
        doctor_id=current_doctor.user_id,
    )
    if not success:
        raise HTTPException(status_code=400, detail=message)
    return {
        "success": True,
        "message": message,
    }


@app.post("/api/v1/consultations/{consultation_id}/reassign")
def reassign_consultation_case(
    consultation_id: str,
    req: ReassignCaseRequest,
    current_user: AuthenticatedUser = Depends(require_role([UserRole.DOCTOR, UserRole.ADMIN, UserRole.NURSE])),
):
    """
    Reassign case to another doctor or department with mandatory clinical reason (B5.6, B5.7).
    """
    from app.db import reassign_consultation, get_consultation, get_default_hospital_id
    if current_user.role == UserRole.DOCTOR and not current_user.is_verified_doctor:
        raise HTTPException(status_code=403, detail="Verified clinician registration is required to reassign cases.")
    consultation = get_consultation(consultation_id)
    if not consultation:
        raise HTTPException(status_code=404, detail="Consultation not found.")
    expected_hospital = current_user.hospital_id or get_default_hospital_id()
    if consultation.get("hospital_id") != expected_hospital:
        raise HTTPException(status_code=403, detail="Case belongs to another hospital.")
    success, message, assignment = reassign_consultation(
        consultation_id=consultation_id,
        reassigning_doctor_id=current_user.user_id,
        target_doctor_id=req.target_doctor_id,
        target_department_code=req.target_department,
        reason=req.reason,
        assigning_role=current_user.role.value,
    )
    if not success:
        raise HTTPException(status_code=400, detail=message)
    return {
        "success": True,
        "message": message,
        "assignment": assignment,
    }


@app.post("/api/v1/consultations/{consultation_id}/override-tier")
def override_consultation_urgency_tier(
    consultation_id: str,
    req: OverrideTierRequest,
    current_doctor: AuthenticatedUser = Depends(require_verified_doctor),
):
    """
    Override AI triage urgency tier with mandatory clinical rationale (B5.6, B5.7).
    Logged to append-only audit trail.
    """
    from app.db import override_urgency_tier
    success, message, consultation = override_urgency_tier(
        consultation_id=consultation_id,
        doctor_id=current_doctor.user_id,
        doctor_reg_no=current_doctor.doctor_registration_number,
        new_tier=req.new_tier,
        reason=req.reason,
    )
    if not success:
        raise HTTPException(status_code=400, detail=message)
    return {
        "success": True,
        "message": message,
        "consultation": consultation,
    }


@app.post("/api/v1/doctor/availability")
def toggle_doctor_availability(
    req: DoctorAvailabilityRequest,
    current_doctor: AuthenticatedUser = Depends(require_role([UserRole.DOCTOR])),
):
    """
    Doctor availability toggle for duty scheduling (B5.1, F3.6).
    """
    from app.db import update_doctor_availability
    success = update_doctor_availability(
        doctor_id=current_doctor.user_id,
        is_on_duty=req.is_on_duty,
    )
    return {
        "success": success,
        "is_on_duty": req.is_on_duty,
        "message": f"Duty status updated to {'ON-DUTY' if req.is_on_duty else 'OFF-DUTY'}.",
    }


@app.get("/api/v1/doctor/roster")
@app.get("/api/v1/admin/roster")
def get_doctor_roster(
    department: Optional[str] = None,
    on_duty_only: bool = False,
    current_user: AuthenticatedUser = Depends(require_role([UserRole.DOCTOR, UserRole.NURSE, UserRole.ADMIN])),
):
    """
    Retrieve hospital doctor roster with specialty, schedule, capacity and duty status (B5.1, F4.2).
    """
    from app.db import get_staff_doctors, get_default_hospital_id
    hospital_id = current_user.hospital_id or get_default_hospital_id()
    doctors = get_staff_doctors(
        department_code=department,
        on_duty_only=on_duty_only,
        hospital_id=hospital_id,
    )
    sanitized = []
    for d in doctors:
        doc_copy = dict(d)
        doc_copy.pop("password_hash", None)
        sanitized.append(doc_copy)
    return {
        "count": len(sanitized),
        "doctors": sanitized,
    }


@app.get("/api/v1/admin/routing-logs")
def get_admin_routing_logs(
    current_admin: AuthenticatedUser = Depends(require_role([UserRole.ADMIN])),
):
    """
    Hospital administrator routing audit logs (B5.7, F4.1).
    """
    from app.db import _MEM_AUDIT_LOGS, get_supabase_client
    client = get_supabase_client()
    if client:
        try:
            res = client.table("audit_logs").select("*").ilike("action", "%ROUTE%").order("created_at", desc=True).limit(50).execute()
            if res.data:
                return {"count": len(res.data), "logs": res.data}
        except Exception:
            pass
    routing_logs = [l for l in _MEM_AUDIT_LOGS if "ROUTE" in l.get("action", "").upper()]
    return {"count": len(routing_logs), "logs": routing_logs}


@app.post("/api/v1/staff/doctors/{doctor_id}/verify-registration")
def verify_doctor_registration(
    doctor_id: str,
    current_admin: AuthenticatedUser = Depends(require_role([UserRole.ADMIN])),
):
    """
    Hospital admin manually verifies doctor registration credentials (B5.2, F4.2).
    Replaces regex-only matching with human administrative sign-off.
    """
    from app.db import verify_doctor_registration_by_admin
    success = verify_doctor_registration_by_admin(
        doctor_id=doctor_id,
        admin_user_id=current_admin.user_id,
    )
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Doctor account not found.")
    return {
        "success": success,
        "message": f"Doctor registration credentials verified by hospital administrator {current_admin.user_id}.",
    }


@app.post("/api/v1/consultations/escalate-unclaimed")
def trigger_unclaimed_escalations(
    current_user: AuthenticatedUser = Depends(require_role([UserRole.DOCTOR, UserRole.ADMIN])),
):
    """
    Evaluate overdue cases; emergency cases still require manual human follow-up.
    """
    if current_user.role == UserRole.DOCTOR and not current_user.is_verified_doctor:
        raise HTTPException(status_code=403, detail="Verified clinician registration is required to review overdue cases.")
    from app.db import get_unclaimed_consultations_for_escalation
    escalations = get_unclaimed_consultations_for_escalation()
    return {
        "count": len(escalations),
        "escalations": escalations,
    }


# -----------------------------------------------------------------------------
# Data Retention & Statutory Erasure (H4.1 - H4.7)
# -----------------------------------------------------------------------------

@app.post("/api/v1/admin/retention/sweep")
def trigger_admin_retention_sweep(
    current_admin: AuthenticatedUser = Depends(require_role([UserRole.ADMIN])),
):
    """Admin-only endpoint to trigger an immediate statutory audio retention sweep (H4.2)."""
    from app.security.data_retention import get_retention_manager
    purged = get_retention_manager().execute_retention_sweep()
    return {
        "status": "success",
        "purged_count": len(purged),
        "purged_files": purged,
        "message": f"Statutory retention sweep completed. Purged {len(purged)} expired audio files.",
    }


@app.get("/api/v1/admin/retention/status")
def get_admin_retention_status(
    current_user: AuthenticatedUser = Depends(require_role([UserRole.ADMIN, UserRole.DOCTOR, UserRole.NURSE])),
):
    """Retrieve current operational status of data retention and audio queues."""
    from app.security.data_retention import get_retention_manager
    status_data = get_retention_manager().get_retention_status()
    return status_data


