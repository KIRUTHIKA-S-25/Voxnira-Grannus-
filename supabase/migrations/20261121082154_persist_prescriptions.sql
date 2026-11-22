ALTER TABLE public.consultations
  ADD COLUMN IF NOT EXISTS prescriptions jsonb NOT NULL DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS prescribed_by text,
  ADD COLUMN IF NOT EXISTS doctor_registration_number text,
  ADD COLUMN IF NOT EXISTS prescription_timestamp timestamptz,
  ADD COLUMN IF NOT EXISTS referral_hospital text;
