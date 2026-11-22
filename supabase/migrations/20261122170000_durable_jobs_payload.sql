-- Enable durable audio recovery across worker restarts and crashes.
-- Encrypted audio payload is stored safely using AES-GCM encoded text until the job is COMPLETED or FAILED.
ALTER TABLE public.pipeline_jobs
  ADD COLUMN IF NOT EXISTS encrypted_audio_payload text,
  ADD COLUMN IF NOT EXISTS audio_filename text,
  ADD COLUMN IF NOT EXISTS language_code text,
  ADD COLUMN IF NOT EXISTS patient_context jsonb,
  ADD COLUMN IF NOT EXISTS doctor_preferred_language text,
  ADD COLUMN IF NOT EXISTS worker_id text,
  ADD COLUMN IF NOT EXISTS locked_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS attempts integer DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_pipeline_jobs_status_recovery
  ON public.pipeline_jobs(status, updated_at)
  WHERE status IN ('QUEUED', 'PREPROCESSING', 'TRANSCRIBING', 'EXTRACTING', 'TRIAGING');
