ALTER TABLE public.guest_cases
  ADD COLUMN IF NOT EXISTS age_ciphertext text,
  ADD COLUMN IF NOT EXISTS location_ciphertext text;
ALTER TABLE public.emergency_sos_dispatches
  ADD COLUMN IF NOT EXISTS location_ciphertext text;
ALTER TABLE public.locations
  ADD COLUMN IF NOT EXISTS location_ciphertext text;
ALTER TABLE public.patients
  ADD COLUMN IF NOT EXISTS phi_ciphertext text;
ALTER TABLE public.audio_retention_queue
  ADD COLUMN IF NOT EXISTS file_path_lookup_hash text;

CREATE UNIQUE INDEX IF NOT EXISTS audio_retention_queue_file_path_lookup_hash_key
  ON public.audio_retention_queue(file_path_lookup_hash)
  WHERE file_path_lookup_hash IS NOT NULL;
