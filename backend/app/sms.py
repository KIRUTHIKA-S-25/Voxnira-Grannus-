"""
SMS Gateway Service for Grannus RuralCare AI.
Integrates with:
  - Twilio (SMS & Emergency Notifications)
  - Sandbox / Local Console Gateway (Dev mode)
"""
import logging
import re
import urllib.parse
import urllib.request
import base64
from typing import Tuple, Optional

from app.config import get_settings

logger = logging.getLogger("rural_care.sms")

# Internal development & demo test numbers that bypass external SMS network
SANDBOX_TEST_NUMBERS = {
    "+919876543210",
    "9876543210",
    "+919999999999",
    "9999999999",
}


def normalize_mobile_for_india(phone: str) -> str:
    """Normalize phone number to international E.164-compatible format: 919876543210."""
    clean = re.sub(r"[^\d]", "", phone.strip())
    if len(clean) == 10:
        return f"91{clean}"
    if clean.startswith("91") and len(clean) == 12:
        return clean
    if clean.startswith("0") and len(clean) == 11:
        return f"91{clean[1:]}"
    return clean


def send_sms(phone_number: str, message: str) -> Tuple[bool, str]:
    """
    Dispatch SMS notification (e.g. for Emergency SOS contact alerts).
    Returns: (success: bool, user_message: str)
    """
    settings = get_settings()
    phone_clean = phone_number.strip()
    is_dev = settings.env in ("development", "test") or settings.allow_dev_otp

    # 1. Gate sandbox test numbers behind development
    if is_dev and (phone_clean in SANDBOX_TEST_NUMBERS or phone_clean.replace("+", "") in SANDBOX_TEST_NUMBERS):
        logger.info("Sandbox test number (%s) detected in dev mode. Skipping external SMS.", phone_clean)
        return True, "Sandbox SMS notification simulated successfully."

    provider = (settings.sms_provider or "console").lower().strip()

    # 2. Dispatch via Twilio (if configured)
    if provider == "twilio" and settings.twilio_account_sid and settings.twilio_auth_token:
        try:
            url = f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_account_sid}/Messages.json"
            to_phone = phone_clean if phone_clean.startswith("+") else f"+{phone_clean}"
            post_data = urllib.parse.urlencode({
                "To": to_phone,
                "From": settings.twilio_phone_number,
                "Body": message,
            }).encode("utf-8")

            auth_str = f"{settings.twilio_account_sid}:{settings.twilio_auth_token}"
            auth_b64 = base64.b64encode(auth_str.encode()).decode()

            req = urllib.request.Request(
                url,
                data=post_data,
                headers={"Authorization": f"Basic {auth_b64}"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=8.0) as response:
                if response.getcode() in (200, 201):
                    logger.info("Twilio SMS dispatched successfully to %s", to_phone)
                    return True, "SMS notification sent."
                else:
                    logger.error("Twilio SMS failed with HTTP status %s", response.getcode())
                    return False, "Twilio SMS dispatch failed."
        except Exception as exc:
            logger.error("Twilio SMS dispatch failed: %s", exc)
            return False, "Twilio gateway connection failed."

    # 3. Local / Console fallback
    if is_dev or provider == "console":
        logger.info("SMS Gateway Console Mode: Message for %s: %s", phone_clean, message)
        return True, "SMS notification logged to console (dev mode)."

    return False, "No active SMS provider configured for production."


def send_sms_otp(phone_number: str, otp_code: str) -> Tuple[bool, str]:
    """
    Legacy SMS OTP dispatcher maintained for backward compatibility.
    Patients now use direct 4-digit PIN authentication.
    """
    settings = get_settings()
    phone_clean = phone_number.strip()
    is_dev = settings.env in ("development", "test") or settings.allow_dev_otp

    if is_dev:
        logger.info("SMS Gateway Console Mode (Dev): OTP for %s is %s", phone_clean, otp_code)
        return True, "Verification code sent. Sandbox test code active (Code: 123456)."

    return send_sms(phone_number, f"Your Grannus verification code is {otp_code}. Valid for 5 minutes.")
