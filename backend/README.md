# Grannus API

The Grannus backend is a FastAPI service for voice intake, AI-assisted clinical summarization and priority support, clinician workflows, and FHIR R4 export. It uses Sarvam AI for speech services, Google Gemini for structured extraction and language tasks, and Supabase for persistence.

> Grannus supports clinical review workflows; it does not diagnose or replace a qualified clinician. Evaluation data in this repository is synthetic and is not evidence of real-world clinical effectiveness.

## Requirements

- Python 3.10 or newer (the production Docker image and CI use Python 3.12)
- `libsndfile` and FFmpeg for audio handling (included in the Docker image)
- Sarvam and Gemini API credentials for AI-backed processing
- A Supabase project configured with the repository's schema and migrations

## Local setup

From the repository root:

```bash
cd backend
python -m venv .venv
# macOS/Linux
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and set the values for your environment. The full application needs `SARVAM_API_KEY`, `GEMINI_API_KEY`, `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `DEFAULT_HOSPITAL_ID`, `JWT_SECRET_KEY`, and `PHI_ENCRYPTION_KEY`. Use unique, strong secrets; do not commit `.env` or production credentials.

Start the API from the `backend` directory:

```bash
uvicorn app.main:app --reload --port 8000
```

- Health: `GET http://localhost:8000/health`
- Readiness: `GET http://localhost:8000/ready`
- Interactive API documentation: `http://localhost:8000/docs`

## Main workflows

1. Receive a patient voice recording or audio submission.
2. Preprocess audio and request speech transcription/translation.
3. Extract structured information and run deterministic safety checks.
4. Produce an explainable HIGH / MEDIUM / LOW priority assessment using the ML model when available, with rule-based fallback.
5. Persist consultation data for review in the clinician interface.
6. Support translated clinician replies, consultation actions, and FHIR R4 bundle export.

Acoustic indicators are experimental and advisory; they are not a standalone diagnosis or emergency trigger. Clinical decisions remain with qualified care professionals.

## Configuration

See `.env.example` for the complete backend configuration template. Key settings include:

| Variable | Purpose |
| --- | --- |
| `SARVAM_API_KEY` | Speech transcription and speech services |
| `GEMINI_API_KEY` | Structured extraction and supported language tasks |
| `SUPABASE_URL` | Supabase project endpoint |
| `SUPABASE_ANON_KEY` | Supabase public key used where applicable |
| `SUPABASE_SERVICE_ROLE_KEY` | Backend-only privileged database access; keep secret |
| `DEFAULT_HOSPITAL_ID` | Default hospital/tenant identifier |
| `JWT_SECRET_KEY` | Signs application session tokens |
| `PHI_ENCRYPTION_KEY` | Stable encryption key for protected stored fields |
| `ALLOWED_ORIGINS` | Allowed frontend origins for API requests |

Never expose service-role, JWT, or encryption keys to the frontend. Changing or losing the PHI encryption key can make protected stored values unreadable. Configure and apply database migrations carefully for the target Supabase project; consult the [deployment runbook](../docs/DEPLOYMENT_RUNBOOK.md), which documents migration-history considerations.

## Tests and evaluation

Install the development test tools if needed, then run from `backend/`:

```bash
pip install pytest pytest-cov pytest-random-order
pytest tests/ -v
```

The repository's CI also runs a synthetic clinical safety gate. See [`eval/README.md`](eval/README.md) and the root [clinical evaluation report](../CLINICAL_VALIDATION_REPORT.md) for the evaluation scope and limitations.

## Docker

The root `docker-compose.yml` starts the backend and frontend together. Configure the required variables in the root environment file before running:

```bash
docker compose up --build
```

See the [deployment runbook](../docs/DEPLOYMENT_RUNBOOK.md) for production configuration and operational guidance. For the web application, see [`frontend/README.md`](../frontend/README.md); for overall architecture and project scope, see the [root README](../README.md).
