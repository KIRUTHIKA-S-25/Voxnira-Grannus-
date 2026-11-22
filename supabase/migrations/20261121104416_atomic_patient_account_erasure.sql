CREATE OR REPLACE FUNCTION public.erase_patient_account_atomic(
  p_account_id uuid,
  p_anonymized_phone text
) RETURNS jsonb
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path TO 'public', 'pg_temp'
AS $function$
DECLARE
  v_profile_ids uuid[];
  v_profile_count integer := 0;
BEGIN
  PERFORM pg_advisory_xact_lock(hashtextextended(p_account_id::text, 0));

  PERFORM 1 FROM public.accounts WHERE id = p_account_id FOR UPDATE;
  IF NOT FOUND THEN
    RETURN jsonb_build_object('success', false, 'profiles_anonymized', 0);
  END IF;

  SELECT coalesce(array_agg(id), ARRAY[]::uuid[])
  INTO v_profile_ids
  FROM public.patient_profiles
  WHERE account_id = p_account_id;
  v_profile_count := coalesce(array_length(v_profile_ids, 1), 0);

  UPDATE public.patient_profiles
  SET full_name = 'ANONYMIZED_PATIENT', age = NULL, gender = NULL,
      guardian_name = NULL, allergies = ARRAY[]::text[],
      medications = ARRAY[]::text[], known_conditions = ARRAY[]::text[],
      pin_hash = NULL, caregiver_relationship = NULL, account_id = NULL,
      updated_at = now()
  WHERE id = ANY(v_profile_ids);

  UPDATE public.consultations
  SET account_id = NULL, profile_id = NULL, updated_at = now()
  WHERE account_id = p_account_id OR profile_id = ANY(v_profile_ids);

  DELETE FROM public.persistent_voice_threads
  WHERE patient_profile_id = ANY(v_profile_ids);

  UPDATE public.persistent_follow_ups
  SET profile_id = NULL, patient_status = NULL, patient_feedback = NULL
  WHERE profile_id = ANY(v_profile_ids);

  UPDATE public.consents SET profile_id = NULL
  WHERE profile_id = ANY(v_profile_ids);

  UPDATE public.guest_cases SET account_id = NULL
  WHERE account_id = p_account_id;

  UPDATE public.accounts
  SET phone_number = p_anonymized_phone, phone_lookup_hash = NULL,
      pin_hash = NULL, failed_pin_attempts = 0, locked_until = NULL,
      is_active = false, updated_at = now()
  WHERE id = p_account_id;

  RETURN jsonb_build_object('success', true, 'profiles_anonymized', v_profile_count);
END;
$function$;

REVOKE ALL ON FUNCTION public.erase_patient_account_atomic(uuid, text) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.erase_patient_account_atomic(uuid, text) TO service_role;
