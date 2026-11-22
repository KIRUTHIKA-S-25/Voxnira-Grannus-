ALTER TABLE public.accounts
  ADD COLUMN IF NOT EXISTS phone_lookup_hash text;
ALTER TABLE public.otp_verifications
  ADD COLUMN IF NOT EXISTS phone_lookup_hash text;

CREATE UNIQUE INDEX IF NOT EXISTS accounts_phone_lookup_hash_key
  ON public.accounts(phone_lookup_hash)
  WHERE phone_lookup_hash IS NOT NULL;
CREATE INDEX IF NOT EXISTS otp_verifications_phone_lookup_hash_created_idx
  ON public.otp_verifications(phone_lookup_hash, created_at DESC)
  WHERE is_used = false;
