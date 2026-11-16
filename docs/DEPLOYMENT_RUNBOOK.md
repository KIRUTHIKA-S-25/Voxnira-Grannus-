# Grannus (RuralCare AI) — Production Deployment Runbook & Operational SOP

**System Version:** 2.0.0-production  
**Target Environments:** Primary Health Centres (PHC), Community Health Centres (CHC), Sub-Centres (Ayushman Arogya Mandir)  
**Security Level:** Production Healthcare Architecture (DPDP Act 2023 & Telemedicine Practice Guidelines 2020 Compliant)

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
│   - Offline IndexedDB Queue for low-connectivity rural areas           │
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
│   - Multi-Profile Phone OTP Service & Caregiver Role Isolation         │
│   - 72-Hour Raw Voice Expiry & Cryptographic Shredding Worker          │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │ Encrypted SQL & Storage APIs
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│                 SUPABASE INFRASTRUCTURE (PostgreSQL)                  │
│   - Row Level Security (RLS) on all patient & clinical tables          │
│   - Encrypted PHI Fields (AES-CBC / Fernet envelope encryption)        │
│   - Immutable Audit Logging (`audit_logs`)                             │
│   - Storage Buckets: `audio-consultations` (72h retention lifecycle)   │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Environment Configuration Checklist

### 2.1 Backend (`backend/.env`)

Ensure the following variables are defined before launching the container:

| Variable | Requirement | Description |
|---|---|---|
| `ENV` | `production` | Environment mode |
| `SARVAM_API_KEY` | Required | Sarvam AI API credentials for Saaras STT & Bulbul TTS |
| `GEMINI_API_KEY` | Required | Google Gemini API credentials for clinical summarization |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Primary clinical decision support LLM model |
| `SUPABASE_URL` | Required | Supabase PostgreSQL project URL |
| `SUPABASE_ANON_KEY` | Required | Supabase anon key for public endpoints |
| `SUPABASE_SERVICE_ROLE_KEY` | Required (Backend Only) | Elevated backend key for bypassing RLS in background jobs |
| `JWT_SECRET_KEY` | Required (>= 32 chars) | Secret key for signing clinician & patient session JWTs |
| `PHI_ENCRYPTION_KEY` | Required (32-byte base64) | Key used for encrypting patient names and phone numbers |
| `AUDIO_RETENTION_HOURS` | `72` | DPDP Act 2023 maximum raw voice retention window |
| `SMS_PROVIDER` | `msg91` / `twilio` | Gateway for sending patient consultation SMS notifications |
| `ALLOW_DEV_OTP` | `false` | MUST BE `false` in production to prevent bypass |

### 2.2 Frontend (`frontend/.env.local`)

| Variable | Requirement | Description |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | `https://api.yourdomain.org` | Backend FastAPI base URL |
| `NEXT_PUBLIC_SUPABASE_URL` | `https://your-project.supabase.co` | Supabase project URL |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Required (Public) | Supabase anon public key |

---

## 3. Database Migration & RLS Deployment

Run all migration scripts in `supabase/migrations/` sequentially via Supabase CLI or PostgreSQL SQL editor:

```bash
# 1. Apply schema and tables
supabase db push

# 2. Verify all RLS policies are active:
SELECT tablename, rowsecurity FROM pg_tables WHERE schemaname = 'public';
```

Required active policies:
- `consultations`: Read/write locked to owning user profile or assigned doctor.
- `patient_profiles`: Read/write locked to primary account phone owner.
- `doctor_queue_assignments`: Locked to verified medical officers.
- `audit_logs`: Append-only, doctor/admin read-only.

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
# Check backend health
curl -f http://localhost:8000/health

# Check metrics
curl -f http://localhost:8000/api/v1/metrics
```

Expected `/health` response:
```json
{
  "status": "healthy",
  "version": "2.0.0",
  "environment": "production",
  "storage": "connected"
}
```

---

## 5. Background Retention Worker Setup (DPDP Act 2023)

In accordance with Section 8(7) of the Digital Personal Data Protection Act 2023, raw patient voice recordings must be permanently shredded after 72 hours.

Configure an hourly cron job (or cloud scheduler like AWS EventBridge / Cloudflare Worker / GitHub Actions) to trigger the retention cleaner:

```bash
# Hourly Retention Cleanup Cron Example:
0 * * * * curl -X POST "https://api.yourdomain.org/api/v1/retention/clean-expired" \
  -H "Authorization: Bearer <SUPABASE_SERVICE_ROLE_KEY>" \
  -H "Content-Type: application/json"
```

The cleanup service will:
1. Scan for consultations where `created_at < NOW() - 72 hours`.
2. Delete raw audio blobs from Supabase Storage bucket `audio-consultations`.
3. Clear `audio_url` and `raw_audio_path` in PostgreSQL.
4. Record verified deletion audit logs in `audit_logs`.

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
3. If GPS was enabled with user consent, coordinates are attached for emergency fleet dispatch.

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

## 8. Quality Gate Summary (Phase 7 Verified)
- **Backend Test Suite:** 232 Passed, 0 Failed, 4 Skipped (Coverage: 78% across 5,195 statements).
- **Frontend Type Safety:** `npx tsc --noEmit` exited with 0 errors.
- **Frontend Linting:** `npm run lint` passed with 0 errors.
- **Production Build:** `npm run build` compiled 8 static routes cleanly in Next.js 16 Turbopack.
- **Security Audit:** Zero hardcoded private keys or production credentials in source code.
