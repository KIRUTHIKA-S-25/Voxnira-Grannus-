-- Link newly recorded consent to the authenticated account or guest emergency
-- case. Keep legacy patient_id/profile_id columns so older records remain intact.
ALTER TABLE public.consents
  ADD COLUMN IF NOT EXISTS owner_type text,
  ADD COLUMN IF NOT EXISTS owner_id text,
  ADD COLUMN IF NOT EXISTS account_id uuid REFERENCES public.accounts(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS guest_case_id uuid REFERENCES public.guest_cases(id) ON DELETE SET NULL;

ALTER TABLE public.consents
  ADD CONSTRAINT consents_owner_type_check
    CHECK (owner_type IS NULL OR owner_type IN ('patient_account', 'guest_case')),
  ADD CONSTRAINT consents_owner_link_check
    CHECK (
      (owner_type IS NULL AND owner_id IS NULL)
      OR (owner_type = 'patient_account' AND owner_id IS NOT NULL AND account_id IS NOT NULL AND guest_case_id IS NULL)
      OR (owner_type = 'guest_case' AND owner_id IS NOT NULL AND guest_case_id IS NOT NULL AND account_id IS NULL)
    );

CREATE INDEX IF NOT EXISTS idx_consents_owner_version
  ON public.consents (owner_type, owner_id, version, granted_at DESC);

-- Account erasure must not retain an owner/profile link to the consent audit
-- row. consents.profile_id is TEXT in the live schema, unlike profile IDs in
-- consultations and follow-ups, so compare UUID profile IDs as text here.
CREATE OR REPLACE FUNCTION public.erase_patient_account_atomic(p_account_id uuid, p_anonymized_phone text)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY INVOKER
 SET search_path TO 'public', 'pg_temp'
AS $function$
DECLARE
  v_profile_ids uuid[];
  v_phone_lookup_hash text;
  v_profile_count integer := 0;
BEGIN
  PERFORM pg_advisory_xact_lock(hashtextextended(p_account_id::text, 0));
  SELECT phone_lookup_hash INTO v_phone_lookup_hash
  FROM public.accounts WHERE id = p_account_id FOR UPDATE;
  IF NOT FOUND THEN
    RETURN jsonb_build_object('success', false, 'profiles_anonymized', 0);
  END IF;

  SELECT coalesce(array_agg(id), ARRAY[]::uuid[]) INTO v_profile_ids
  FROM public.patient_profiles WHERE account_id = p_account_id;
  v_profile_count := coalesce(array_length(v_profile_ids, 1), 0);

  UPDATE public.patient_profiles
  SET full_name = 'ANONYMIZED_PATIENT', age = NULL, gender = NULL,
      guardian_name = NULL, allergies = ARRAY[]::text[], medications = ARRAY[]::text[],
      known_conditions = ARRAY[]::text[], pin_hash = NULL, caregiver_relationship = NULL,
      account_id = NULL, updated_at = now()
  WHERE id = ANY(v_profile_ids);
  UPDATE public.consultations
  SET account_id = NULL, profile_id = NULL, updated_at = now()
  WHERE account_id = p_account_id OR profile_id = ANY(v_profile_ids);
  DELETE FROM public.persistent_voice_threads WHERE patient_profile_id = ANY(v_profile_ids);
  UPDATE public.persistent_follow_ups
  SET profile_id = NULL, patient_status = NULL, patient_feedback = NULL
  WHERE profile_id = ANY(v_profile_ids);
  UPDATE public.consents
  SET profile_id = NULL, account_id = NULL, owner_id = NULL, owner_type = NULL
  WHERE account_id = p_account_id
     OR profile_id IN (SELECT unnest(v_profile_ids)::text);
  UPDATE public.guest_cases SET account_id = NULL WHERE account_id = p_account_id;

  IF v_phone_lookup_hash IS NOT NULL THEN
    DELETE FROM public.otp_verifications WHERE phone_lookup_hash = v_phone_lookup_hash;
  END IF;
  UPDATE public.accounts
  SET phone_number = p_anonymized_phone, phone_lookup_hash = NULL,
      pin_hash = NULL, failed_pin_attempts = 0, locked_until = NULL,
      is_active = false, updated_at = now()
  WHERE id = p_account_id;
  INSERT INTO public.deletion_log (resource_type, resource_id, reason, metadata)
  VALUES (
    'patient_account',
    'sha256:' || encode(digest(p_account_id::text, 'sha256'), 'hex'),
    'Patient account data erased or anonymized',
    jsonb_build_object('profiles_anonymized', v_profile_count)
  );
  RETURN jsonb_build_object('success', true, 'profiles_anonymized', v_profile_count);
END;
$function$;

REVOKE ALL ON FUNCTION public.erase_patient_account_atomic(uuid, text) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.erase_patient_account_atomic(uuid, text) TO service_role;
