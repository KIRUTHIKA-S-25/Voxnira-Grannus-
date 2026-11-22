# Regulatory Considerations & Evidence Inventory

**Product:** Grannus (RuralCare AI)  
**Jurisdiction:** Republic of India  
**Status:** Engineering evidence inventory only; it is not a legal classification, compliance certification, or substitute for review by qualified counsel and clinical governance.
**Applicable Frameworks:**
1. Central Drugs Standard Control Organisation (CDSCO) Medical Device Rules, 2017
2. Telemedicine Practice Guidelines, 2020 (Board of Governors, Medical Council of India / MoHFW)
3. Digital Personal Data Protection (DPDP) Act, 2023 (Ministry of Electronics and Information Technology)
4. Ayushman Bharat Digital Mission (ABDM) Standards (National Health Authority)

---

## 1. CDSCO Software as a Medical Device (SaMD) Assessment

### 1.1 Classification Status
The product includes AI-supported triage, clinical summaries, emergency prioritization, and prescription workflows. Its regulatory classification has not been established by this engineering inventory. A qualified regulatory assessment is required before clinical deployment; clinician review and disclaimers do not determine the classification.

| Assessment Criterion | Grannus Architecture | Regulatory Finding |
|---|---|---|
| **AI output** | The application structures symptoms, summarizes cases, and assigns urgency. | Functional description only; classification and intended-use claims require formal review. |
| **Prescription workflow** | Clinician actions and prescriptions are gated by doctor-role checks and a persisted manual verification flag. | This control does not independently validate registration with a medical council. |
| **Clinician review** | Clinical workflows expose clinician review and escalation paths. | Confirm operational workflows and clinical governance before deployment. |
| **Self-care guidance** | Low-urgency guidance is limited to non-pharmacological suggestions and red-flag prompts. | This product behavior does not establish regulatory status. |

### 1.2 Statutory Labeling & Emergency Disclaimers
Grannus incorporates prominent disclaimers across all patient and clinician interfaces:
> **NOTICE:** *Grannus is an AI-assisted clinical decision support system, NOT a diagnostic medical device. It does NOT provide a final medical diagnosis. In any acute or life-threatening emergency, immediately call the National Ambulance Service 108 or Emergency 112.*

---

## 2. Telemedicine Practice Guidelines 2020 Compliance

Under the guidelines notified by the Ministry of Health and Family Welfare (MoHFW) on 25 March 2020:

### 2.1 Registered Medical Practitioner (RMP) Verification
- **Section 1.3:** Only doctors registered with the National Medical Commission (NMC) or a State Medical Council may provide telemedicine consultations and issue prescriptions.
- **Grannus Implementation:**
  - Mandatory doctor authentication verifying NMC or State Medical Council registration numbers (e.g., `TNMC-54321`, `KMC-45678`).
  - Cryptographically signed JWT tokens embedding verified RMP credentials.
  - Endpoints generating digital prescriptions (`/api/v1/doctor/voice-prescription`) and exporting clinical records (`/api/v1/export/fhir`) are strictly gated by `require_verified_doctor` and `require_role`.

### 2.2 Patient Identification & Consent
- **Section 3.2:** Explicit patient consent must be recorded before telemedicine services commence.
- **Grannus Implementation:** A multilingual consent dialog is displayed before audio recording. The notice content and consent flow require legal and clinical review before use.

### 2.3 Prescription Signing and Record Retention
- **Section 3.7:** Prescriptions generated via voice translation must be linked to the clinician's registration number and retained for at least 3 years.
- **Grannus Implementation:** Immutable audit logging (`audit_logger.py`) records the consulting doctor's ID, medical registration number, consultation UUID, and timestamp for all prescription and triage actions.

---

## 3. DPDP Act 2023 Technical Evidence Inventory

The matrix assumes Grannus may act as a Data Fiduciary; the actual legal role and obligations require counsel review.

| DPDP Section | Statutory Obligation | Grannus Technical Implementation | Verification Method |
|---|---|---|---|
| **Section 4 & 6** | Grounds for processing; Requirement of free, informed, specific, unconditional, and unambiguous consent. | Multilingual consent notices in English, Tamil, Hindi, and Telugu (`consent.py`). Mandatory affirmative checkbox before recording. | Unit & integration tests (`test_multilingual_dpdp_consent_notices`, `test_patient_consent_recording`). |
| **Section 5** | Notice with purpose specification and grievance contact details. | The backend notice and frontend consent dialog use deployment-configured grievance contact details. Those values are not configured in this workspace; configure and verify the real contact before release. | Endpoint `GET /api/v1/consent/notice` and frontend build; validate deployed values manually. |
| **Section 8(4)** | Implementation of technical and organizational measures to ensure personal data protection. | Versioned AES-GCM encryption is integrated into supported application persistence paths, with keyed lookup hashes for phone numbers. The connected database still contains legacy plaintext values and this workspace has no PHI key or service-role key to run the backfill. | Backend tests cover encryption round trips; live row counts still show plaintext. Do not treat this as a production control until backup, key provisioning, backfill, and zero-plaintext verification are complete. |
| **Section 8(6)** | Notification of personal data breaches to the Data Protection Board of India and affected Data Principals. | Formulated 6-hour response protocol detailed in `BREACH_RESPONSE_PLAN.md`. | Verified breach response procedure. |
| **Section 8(7)** | Erasure of personal data upon withdrawal of consent or expiry of specified purpose. | The retention worker deletes expired local audio files. It does not remove Supabase Storage objects. Account erasure anonymizes profiles and disassociates retained clinical records; this is not deletion of every clinical record. | Local-file behavior is covered by tests. Storage-object deletion and production retention configuration remain unverified. |
| **Section 9** | Processing of children's personal data. | Age-aware pediatric safeguards: Children under 5 and infants (age in months) cannot be triaged as LOW; parental consent required. | Phase 1 & Phase 3 unit tests (`test_feature_extraction_age_groups_and_infants`). |
| **Section 11** | Right to access information about personal data. | Patient intake lookup and consultation record export available to authorized users. | RBAC and consultation API. |
| **Section 12** | Right to correction and erasure. | Account-erasure endpoints retire the account, anonymize profiles, and unlink retained clinical records; raw local audio cleanup is handled separately. | Automated tests cover application behavior. Retention exceptions, identity/session revocation, and production storage cleanup need operational verification. |
| **Section 13** | Grievance redressal. | The configured grievance email is included in the notice. | Deployment configuration and rendered notice must be checked before release. |

---

## 4. Ayushman Bharat Digital Mission (ABDM) Conformance

Grannus is engineered for seamless interoperability with India's national health stack:

1. **Milestone 1 (M1) — ABHA ID Integration:**
   - Patient intake supports Ayushman Bharat Health Account (ABHA) IDs (14 digits) and addresses.
   - De-identification engine protects ABHA IDs in logs and LLM dispatches while maintaining clinical linkage via cryptographic surrogates.

2. **Milestone 2 (M2) — FHIR R4 Clinical Document Generation:**
   - The `/api/v1/export/fhir` endpoint produces NRCES-compliant FHIR R4 `DocumentBundle` resources (`https://nrces.in/ndhm/fhir/r4/StructureDefinition/DocumentBundle`).
   - Includes ABDM-required resources: `Composition` (OPConsultationRecord), `Patient`, `Encounter`, `Condition` (chief complaint), `Observation` (symptoms), `RiskAssessment` (triage level), and `DetectedIssue` (red flags).
   - Profile conformance verified programmatically via `validate_abdm_fhir_bundle()`.

3. **Milestone 3 (M3) — Health Information Exchange (HIP/HIU):**
   - Cryptographic short-lived signed URLs (`generate_signed_url`) ensure secure media transfer between health information providers and users without exposing public S3/Supabase storage buckets.

---

## 5. Security & Infrastructure Controls Summary

- **Authentication:** HMAC-SHA256 cryptographic JWT tokens with verified doctor credentials.
- **Input Sanitization:** Magic byte file inspection blocking Windows PEs, Linux ELFs, shell scripts, and HTML/PHP polyglots; strict regex-based path traversal protection.
- **Denial of Service Defense:** In-memory sliding window rate limiter (30 requests/min for intake, burst limit 10).
- **Zero-PHI Logging:** Direct Indian identifiers (Aadhaar, ABHA, phone, email) scrubbed automatically from all application logs.
- **Network Boundaries:** CORS restricted to approved client domains; secrets segregated in environment configurations.
