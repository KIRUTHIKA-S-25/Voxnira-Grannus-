# Grannus (RuralCare AI) — Production Deployment Runbook & Operational SOP

**System Version:** 2.0.0-production  
**Target Environments:** Primary Health Centres (PHC), Community Health Centres (CHC), Sub-Centres (Ayushman Arogya Mandir)  
**Status:** Deployment runbook and technical controls; not a legal or clinical compliance certification

---

## 1. System Architecture & Topology

```
┌────────────────────────────────────────────────────────────────────────┐
│                        PATIENT / ASHA WORKER                           │
│     (Tamil, Hindi, Telugu, Kannada, Bengali, Marathi, or English)     │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │ Spoken Voice / Geolocation / SOS
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│                     FRONTEND WEB CLIENT (Next.js 16)                   │
│   - Mobile PWA Interface for Village Health Workers (ASHAs / ANMs)     │
│   - In-memory audio submission; browser storage is not used for audio  │
│   - Doctor Telehealth Triage Portal with RMP Registration Verification │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │ REST APIs / HTTPS
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│                     BACKEND ENGINE (FastAPI / Python 3.12)             │
│   - Audio Preprocessing (16kHz Mono, Noise Reduction, SNR Gate)        │
│   - Sarvam AI Speech-to-Text & Text-to-Speech (Saaras/Bulbul)          │
│   - Gemini 2.5 LLM Clinical Extraction & Decision Support Assist       │
│   - Deterministic Red-Flag Safety Engine & Priority Floor Classifier   │
│   - Multi-Profile Phone + PIN Sign-In & Caregiver Role Isolation       │
│   - Legacy filesystem-audio cleanup worker                             │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │ Encrypted SQL & Storage APIs
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│                 SUPABASE INFRASTRUCTURE (PostgreSQL)                  │
│   - Row Level Security (RLS) on all patient & clinical tables          │
│   - AES-GCM encryption on supported persistence paths (legacy PHI backfill required) │
│   - Immutable Audit Logging (`audit_logs`)                             │
│   - Patient upload audio is transient; legacy filesystem queue is swept │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Environment Configuration Checklist

### 2.1 Docker deployment environment (`.env` at repository root)

Docker Compose reads the root `.env` file. Start from the root `.env.example`, replace every placeholder, and keep secret values in the deployment secret store for hosted environments. `backend/.env` is read when running the Python service directly, outside Compose.

| Variable | Requirement | Description |
|---|---|---|
| `ENV` | `production` | Environment mode |
| `SARVAM_API_KEY` | Required | Sarvam AI API credentials for Saaras STT & Bulbul TTS |
| `GEMINI_API_KEY` | Required | Google Gemini API credentials for clinical summarization |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Primary clinical decision support LLM model |
| `SUPABASE_URL` | Required | Supabase PostgreSQL project URL |
| `DEFAULT_HOSPITAL_ID` | Required (UUID) | Hospital row in this Supabase project used for intake and doctor routing; startup verifies it exists |
| `SUPABASE_ANON_KEY` | Optional in production backend | Only used for non-production backend fallback; public client access uses the frontend's publishable/anon key |
| `SUPABASE_SERVICE_ROLE_KEY` | Required (Backend Only) | Elevated backend key for bypassing RLS in background jobs |
| `JWT_SECRET_KEY` | Required (>= 32 chars) | Secret key for signing clinician & patient session JWTs |
| `PHI_ENCRYPTION_KEY` | Required (>= 32 random characters) | Stable key for versioned AES-GCM field encryption and keyed lookup hashes; losing or changing it makes stored protected values unreadable |
| `AUDIO_RETENTION_HOURS` | `72` | Expiry window used by the legacy filesystem-audio cleanup queue; current patient uploads are not persisted by the app |
| `ALLOW_DEV_OTP` | `false` | MUST BE `false` in production to prevent bypass |
| `ALLOWED_ORIGINS` | Required | Comma-separated HTTPS origins for deployed frontend; localhost origins are rejected in production |
| `GRIEVANCE_OFFICER_NAME` | Required | Actual designated grievance officer name returned by the backend consent notice |
| `GRIEVANCE_OFFICER_EMAIL` | Required | Actual designated grievance contact returned by the backend consent notice |

Before launch, verify the Sarvam workspace's data-retention setting and model-training preference with the account owner. Sarvam documents workspace-level retention controls and an opt-in default for model improvement; an API key alone does not prove zero retention or training opt-out. See the [Sarvam API FAQ](https://docs.sarvam.ai/api/platform/faq) and [Sarvam privacy policy](https://www.sarvam.ai/privacy-policy), then apply the settings and terms agreed for this deployment.

### 2.2 Frontend build variables (root `.env`)

| Variable | Requirement | Description |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | `https://api.yourdomain.org` | Backend FastAPI base URL |
| `NEXT_PUBLIC_SUPABASE_URL` | `https://your-project.supabase.co` | Supabase project URL |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Required (Public) | Supabase anon public key |
| `NEXT_PUBLIC_GRIEVANCE_OFFICER_EMAIL` | Required | Actual public grievance contact displayed in the consent dialog; must match backend configuration |

Set all `NEXT_PUBLIC_*` values in the root `.env` before building the frontend image. Next.js compiles them into the browser bundle; changing only container runtime variables does not update them. The provided Supabase project URL is `https://cvnuaxcyadfycsfkeftu.supabase.co`.

Outbound SMS is disabled. Patient authentication uses phone number and PIN. Emergency pages instruct patients to call 108 or 112 directly; the application does not message emergency contacts or dispatch emergency services.

---

## 3. Database Migration & RLS Deployment

The live project and this checkout currently have migration-history drift. A read-only comparison on 2026-11-21 found seven versions recorded remotely but missing from `supabase/migrations/`:

| Remote version | Recorded name |
|---|---|
| `20260831171351` | `init_grannus_schema` |
| `20260901155750` | `add_pipeline_results_table` |
| `20260901155828` | `enable_rls_with_anon_policies` |
| `20261004163330` | `multi_tenant_core_schema_and_persistence` |
| `20261004165817` | `backend_access_policies_for_persistence_tables` |
| `20261004171200` | `enhance_consents_table_with_version_and_metadata` |
| `20261005171612` | `add_guest_cases_and_deletion_log` |

This checkout also has three older migration files that are not recorded remotely: `20261006_row_level_security.sql`, `20261118_staff_lockouts_and_sos.sql`, and `20261120_patient_phone_pin_auth.sql`. The 19 migrations dated `20261121073314` through `20261121111639` are recorded remotely and have matching local files. Three newer migrations are local only and have not been applied to the live database: `20261121132640_consent_owner_links.sql`, `20261121133800_sos_recording_semantics.sql`, and `20261121134500_pipeline_job_owner_links.sql`. Consent creation/audio submission, accurate SOS status reporting, and guest job ownership require these migrations.

`supabase/schema_snapshot.sql` captures the current public schema for recovery/reference, but is explicitly not a clean-install migration. Do not run `supabase db push` against the linked project or a fresh project until the history is reconciled: the CLI could attempt the three local-only files against the live database, while a fresh project lacks the seven historical schema changes. Recover the original SQL from its source of truth, or agree on and verify a baseline-and-history-repair procedure against a disposable project first. Do not repair remote migration history or replay old migrations against production as an ad hoc fix.

After the local and remote histories match, apply only reviewed forward migrations and verify the recorded history before deployment. The following command is for a synchronized local history only:

```bash
# 1. Apply schema and tables
supabase db push

# 2. Verify all RLS policies are active:
SELECT tablename, rowsecurity FROM pg_tables WHERE schemaname = 'public';
```

The current client policy is deny-by-default for sensitive tables: `anon` and `authenticated` have no table privileges or policies there. Only `hospitals` and `departments` are publicly readable. The backend uses the Supabase service-role key and enforces application authorization; never expose that key to the frontend. Add user-scoped RLS policies before allowing direct authenticated client access to patient data.

The application encrypts newly written values, but the live database still contains legacy plaintext phone numbers, profile names, and consultation transcripts. Take a verified database backup, set the same long-lived `PHI_ENCRYPTION_KEY` that the application will use, and run `python scripts/backfill_phi_encryption.py --dry-run` from `backend/` followed by `python scripts/backfill_phi_encryption.py`. Rerun until the script reports zero rows to update. Preserve that key and coordinate any future rotation with a migration that re-encrypts data and recomputes phone lookup hashes.

The backfill includes the patient, OTP, consultation, clinical-summary, pipeline-result, follow-up, assignment, audit, deletion, location, SOS, and audio-retention fields represented in the current schema. Review any schema extensions or tables added later; this is a versioned field inventory, not automatic encryption of arbitrary database columns.

### 3.1 Durable consultation processing (release blocker)

`POST /api/v1/pipeline/process-audio` currently starts processing with an in-process `asyncio.create_task`. Job progress is persisted in Supabase, but the uploaded audio and executable work item are not stored in a durable queue. If the serving worker exits after accepting the request, the job can remain queued or in progress without a worker able to resume it. The Docker backend also starts multiple Uvicorn workers, while each worker owns separate in-memory job and rate-limit state. Do not deploy this flow for clinical use until processing is moved to a durable queue with recoverable encrypted audio or the API is changed to report completion only after processing; verify restart, duplicate submission, and cross-worker polling behavior before release.

---

## 4. Docker Deployment Instructions

The repository provides a production multi-stage Docker orchestration setup.

### 4.1 Launching with Docker Compose
```bash
# Pull dependencies, build optimized images, and start services
docker compose up -d --build

# Inspect running containers
docker compose ps

# Check logs
docker compose logs -f backend
docker compose logs -f frontend
```

### 4.2 Verifying Container Health
```bash
# Check backend liveness
curl -f http://localhost:8000/health

# Check backend readiness
curl -f http://localhost:8000/ready

# Check metrics
curl -f http://localhost:8000/api/v1/metrics
```

`/health` is a liveness response (it does not assert database connectivity). It returns:
```json
{
  "status": "ok",
  "sarvam_key_configured": true,
  "gemini_key_configured": true,
  "audio_preprocessing_enabled": true,
  "ml_model_loaded": true
}
```

---

## 5. Audio Processing and Provider Retention

Patient uploads are sent to Sarvam AI for transcription. The app processes the upload from the request body and does not persist a raw audio file. The hourly worker remains for legacy paths already recorded in `audio_retention_queue`; it does not control Sarvam's retention and does not delete Supabase Storage objects. The consent notice describes these boundaries. Configure and verify the provider-side retention and model-training settings separately.

Supabase Storage deletion and database `audio_url` cleanup are not implemented because current patient upload routes do not persist audio objects. If persistent audio storage is added later, implement and verify encryption, access control, expiry deletion, and audit logging before enabling it.

---

## 6. Clinical Safety & Medical Officer Protocols

### 6.1 Telemedicine Practice Guidelines 2020 Compliance
- **Registered Medical Practitioner (RMP) Verification:** All clinicians must enter a verified National Medical Commission (NMC) or State Medical Council registration ID (e.g. `TNMC-12345`). Unverified accounts cannot prescribe medications, override triage, or sign referrals.
- **AI Decision Support Disclaimer:** Grannus provides decision support only. AI triage output does not constitute a definitive medical diagnosis. A qualified clinician must independently verify findings before prescribing.
- **Mandatory E-Prescription Fields:** Every issued prescription must include Medicine Name, Dosage/Strength, Frequency, Duration, and Doctor Registration Number.

### 6.2 Emergency Escalation (108 / 112)
If a patient exhibits acute red flags (e.g. cardiac agony, dyspnea, acute neurological deficit, snakebite, obstetric hemorrhage):
1. The app immediately displays an **Emergency SOS Banner** with instant 108/112 dialer buttons.
2. The case is floor-prioritized to `HIGH` (Emergency) across all dashboards.
3. If GPS was enabled with user consent, coordinates are attached to the application alert record. The app does not contact ambulance dispatch; patients must call 108 or 112 directly.

---

## 7. Disaster Recovery & Rollback

### 7.1 Database Backup
Supabase provides automated point-in-time recovery (PITR). For manual daily off-site dumps:
```bash
pg_dump -h <supabase_host> -U postgres -d postgres -F c -b -v -f grannus_backup_$(date +%F).dump
```

### 7.2 Service Rollback
In the event of a deployment defect:
```bash
# Rollback to the previous stable git commit
git checkout <previous-stable-tag>
docker compose down
docker compose up -d --build
```

---

## 8. Quality Gate Summary
- Run the backend and frontend checks against the current commit before each release; historical counts below are not release evidence.
- **Frontend Linting:** ESLint reports warnings that should be triaged; a zero-warning lint result is not currently achieved.
- **Security Audit:** Source review required. The encryption helper does not by itself prove that every PHI database field is encrypted; audit the concrete persistence paths and migrate legacy values before production use.
