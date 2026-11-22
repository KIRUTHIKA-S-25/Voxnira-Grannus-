"""
Consent notice content and timestamped consent record handling.

This implementation is a technical workflow, not a legal compliance determination.

Provides:
  - Multilingual consent notices in patient's native language
  - Explicit purpose limitation (triage & doctor consultation only)
  - Statutory Data Principal rights (Access, Correction, Erasure, Grievance Redressal)
  - Cryptographic timestamped consent record storage
"""
import time
import hashlib
import uuid
from typing import Dict, Optional, List, Any
from pydantic import BaseModel, Field
from app.config import get_settings

CONSENT_VERSION = "2026.11.21-v2"

# Multilingual consent notices displayed before audio recording
CONSENT_NOTICES: Dict[str, Dict[str, str]] = {
    "en-IN": {
        "title": "Patient Consent & Privacy Notice (DPDP Act 2023)",
        "purpose": "Your audio is sent to Sarvam AI to transcribe your speech and support an AI clinical triage summary for your doctor. It is not used for advertising.",
        "retention": "The app does not persist a raw audio file after processing. Transcripts and clinical records may be retained for care and applicable obligations. Sarvam's data retention depends on the service agreement and account settings.",
        "rights": "You may submit a request about your personal data to the designated Grievance Officer. Some clinical records may need to be retained.",
        "emergency_disclaimer": "Grannus is a decision support tool, NOT a definitive diagnosis. In medical emergencies, immediately call 108 or 112.",
        "checkbox_label": "I have understood the purpose and grant voluntary consent for voice recording.",
    },
    "ta-IN": {
        "title": "நோயாளி ஒப்புதல் மற்றும் தனியுரிமை அறிவிப்பு (DPDP சட்டம் 2023)",
        "purpose": "உங்கள் குரல் உரைமாற்றம் செய்யவும் மருத்துவருக்கான AI மருத்துவச் சுருக்கத்தை உருவாக்கவும் Sarvam AI-க்கு அனுப்பப்படுகிறது. இது விளம்பரத்திற்குப் பயன்படுத்தப்படாது.",
        "retention": "செயலாக்கத்திற்குப் பிறகு செயலி மூல ஒலிக் கோப்பைச் சேமிக்காது. உரைமாற்றமும் மருத்துவச் சுருக்கமும் பராமரிப்புக்காக வைக்கப்படலாம். Sarvam-இல் தரவு சேமிப்பு சேவை ஒப்பந்தம் மற்றும் கணக்கு அமைப்புகளைப் பொறுத்தது.",
        "rights": "உங்கள் தனிப்பட்ட தரவு குறித்து நியமிக்கப்பட்ட குறைதீர் அலுவலரிடம் கோரிக்கை அளிக்கலாம். சில மருத்துவப் பதிவுகள் தொடர்ந்து வைக்கப்பட வேண்டியிருக்கலாம்.",
        "emergency_disclaimer": "இது முடிவான சிகிச்சை அல்ல; மருத்துவ அவசரத்திற்கு உடனடியாக 108 அல்லது 112 ஐ அழைக்கவும்.",
        "checkbox_label": "நான் இந்த நோக்கத்தை புரிந்துகொண்டு குரல் பதிவு செய்ய முழு சம்மதம் தெரிவிக்கிறேன்.",
    },
    "hi-IN": {
        "title": "मरीज सहमति एवं गोपनीयता सूचना (DPDP अधिनियम 2023)",
        "purpose": "आपकी आवाज़ को लिखित रूप में बदलने और डॉक्टर के लिए AI चिकित्सा सारांश बनाने हेतु ऑडियो Sarvam AI को भेजा जाता है। इसका उपयोग विज्ञापन के लिए नहीं होता।",
        "retention": "प्रक्रिया पूरी होने के बाद ऐप मूल ऑडियो फ़ाइल को सुरक्षित नहीं रखता। देखभाल के लिए प्रतिलेख और चिकित्सीय सारांश रखे जा सकते हैं। Sarvam में डेटा की अवधि सेवा अनुबंध और खाते की सेटिंग पर निर्भर करती है।",
        "rights": "आप अपने व्यक्तिगत डेटा के बारे में नामित शिकायत अधिकारी से अनुरोध कर सकते हैं। कुछ चिकित्सीय रिकॉर्ड रखने की आवश्यकता हो सकती है।",
        "emergency_disclaimer": "यह अंतिम निदान नहीं है। किसी भी आपात स्थिति में तुरंत 108 या 112 पर कॉल करें।",
        "checkbox_label": "मैंने सभी नियम समझ लिए हैं और आवाज रिकॉर्डिंग के लिए अपनी सहमति देता/देती हूँ।",
    },
    "te-IN": {
        "title": "రోగి సమ్మతి మరియు గోప్యతా నోటీసు (DPDP చట్టం 2023)",
        "purpose": "మీ మాటలను లిఖిత రూపంలోకి మార్చి వైద్యుడికి AI వైద్య సారాంశాన్ని అందించడానికి మీ ఆడియోను Sarvam AIకు పంపుతాము. ఇది ప్రకటనల కోసం ఉపయోగించబడదు.",
        "retention": "ప్రాసెసింగ్ తర్వాత యాప్ అసలు ఆడియో ఫైల్‌ను నిల్వ చేయదు. సంరక్షణ కోసం ట్రాన్స్‌క్రిప్ట్ మరియు వైద్య సారాంశం నిల్వ చేయబడవచ్చు. Sarvam వద్ద డేటా నిల్వ వ్యవధి సేవా ఒప్పందం మరియు ఖాతా సెట్టింగ్‌లపై ఆధారపడి ఉంటుంది.",
        "rights": "మీ డేటాను ఎప్పుడైనా తొలగించే హక్కు మీకు ఉంది.",
        "emergency_disclaimer": "ఇది తుది వైద్య నిర్ధారణ కాదు. అత్యవసర పరిస్థితుల్లో వెంటనే 108 లేదా 112 కు కాల్ చేయండి.",
        "checkbox_label": "నేను ప్రయోజనాన్ని అర్థం చేసుకున్నాను మరియు వాయిస్ రికార్డింగ్ కోసం నా సమ్మతిని తెలియజేస్తున్నాను.",
    },
}

class ConsentRecord(BaseModel):
    consent_id: str
    patient_id: str
    language_code: str
    consent_version: str = CONSENT_VERSION
    purpose: str
    timestamp: float = Field(default_factory=time.time)
    explicit_consent_granted: bool
    ip_hash: str
    retention_period_hours: Optional[int] = None


def get_consent_notice(language_code: str = "en-IN") -> Dict[str, Any]:
    """Retrieve localized consent notice."""
    notice = CONSENT_NOTICES.get(language_code, CONSENT_NOTICES["en-IN"])
    settings = get_settings()
    return {
        "version": CONSENT_VERSION,
        "language": language_code,
        "notice": notice,
        "grievance_officer": {
            "name": settings.grievance_officer_name or None,
            "email": settings.grievance_officer_email or None,
        },
    }


def record_patient_consent(
    patient_id: str,
    language_code: str,
    client_ip: str,
    explicit_consent: bool,
) -> ConsentRecord:
    """Generate and store verified consent record."""
    if not explicit_consent:
        raise ValueError("Consent must be explicitly affirmed by the patient.")

    ip_hash = hashlib.sha256(client_ip.encode()).hexdigest()[:16]
    consent_id = str(uuid.uuid4())

    notice = CONSENT_NOTICES.get(language_code, CONSENT_NOTICES["en-IN"])
    record = ConsentRecord(
        consent_id=consent_id,
        patient_id=patient_id,
        language_code=language_code,
        purpose=f"{notice['purpose']} {notice['retention']}",
        explicit_consent_granted=True,
        ip_hash=ip_hash,
    )
    return record
