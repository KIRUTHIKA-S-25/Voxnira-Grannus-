-- =============================================================================
-- Migration: 20261120_patient_phone_pin_auth.sql
-- Description: Add compulsory 4-digit PIN hash and brute-force lockout columns
--              to accounts table for patient authentication.
-- =============================================================================

ALTER TABLE public.accounts ADD COLUMN IF NOT EXISTS pin_hash TEXT;
ALTER TABLE public.accounts ADD COLUMN IF NOT EXISTS failed_pin_attempts INT NOT NULL DEFAULT 0;
ALTER TABLE public.accounts ADD COLUMN IF NOT EXISTS locked_until TIMESTAMPTZ;

-- Ensure anon cannot query or scrape pin_hash directly
REVOKE SELECT (pin_hash) ON public.accounts FROM anon;
