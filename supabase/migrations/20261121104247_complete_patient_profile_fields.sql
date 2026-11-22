ALTER TABLE public.patient_profiles
  ADD COLUMN IF NOT EXISTS guardian_name text,
  ADD COLUMN IF NOT EXISTS is_caregiver_managed boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS caregiver_relationship text;
