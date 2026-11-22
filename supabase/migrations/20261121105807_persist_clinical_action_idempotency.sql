ALTER TABLE public.doctor_actions
  ADD COLUMN IF NOT EXISTS idempotency_fingerprint text;

CREATE INDEX IF NOT EXISTS doctor_actions_fingerprint_created_idx
  ON public.doctor_actions(consultation_id, idempotency_fingerprint, created_at DESC)
  WHERE idempotency_fingerprint IS NOT NULL;

CREATE OR REPLACE FUNCTION public.save_doctor_action_atomic(p_action jsonb, p_fingerprint text)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY INVOKER
 SET search_path TO 'public', 'pg_temp'
AS $function$
DECLARE
  v_consultation_id uuid;
  v_existing public.doctor_actions%ROWTYPE;
  v_inserted public.doctor_actions%ROWTYPE;
BEGIN
  v_consultation_id := nullif(p_action->>'consultation_id', '')::uuid;
  IF v_consultation_id IS NULL OR p_fingerprint IS NULL OR p_fingerprint = '' THEN
    RAISE EXCEPTION 'Consultation and idempotency fingerprint are required';
  END IF;

  PERFORM pg_advisory_xact_lock(hashtextextended(v_consultation_id::text, 0));

  SELECT * INTO v_existing
  FROM public.doctor_actions
  WHERE consultation_id = v_consultation_id
    AND idempotency_fingerprint = p_fingerprint
    AND created_at >= now() - interval '10 seconds'
  ORDER BY created_at DESC
  LIMIT 1
  FOR UPDATE;

  IF FOUND THEN
    RETURN jsonb_build_object('duplicate', true, 'row', to_jsonb(v_existing));
  END IF;

  INSERT INTO public.doctor_actions (
    id, action_id, consultation_id, doctor_id, action_type, notes, details,
    idempotency_fingerprint, created_at
  ) VALUES (
    (p_action->>'id')::uuid,
    p_action->>'action_id',
    v_consultation_id,
    nullif(p_action->>'doctor_id', '')::uuid,
    p_action->>'action_type',
    p_action->>'notes',
    coalesce(p_action->'details', '{}'::jsonb),
    p_fingerprint,
    coalesce(nullif(p_action->>'created_at', '')::timestamptz, now())
  )
  RETURNING * INTO v_inserted;

  RETURN jsonb_build_object('duplicate', false, 'row', to_jsonb(v_inserted));
END;
$function$;

REVOKE ALL ON FUNCTION public.save_doctor_action_atomic(jsonb, text) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.save_doctor_action_atomic(jsonb, text) TO service_role;
