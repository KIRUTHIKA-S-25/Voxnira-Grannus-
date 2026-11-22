-- Serialize claims across all API workers using a transaction-scoped advisory lock.
CREATE OR REPLACE FUNCTION public.claim_consultation_atomic(
  p_consultation_id uuid,
  p_doctor_id uuid,
  p_doctor_name text,
  p_lock_ttl_minutes integer DEFAULT 15
) RETURNS jsonb
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
  v_assignment public.consultation_assignments%ROWTYPE;
  v_now timestamptz := now();
BEGIN
  PERFORM pg_advisory_xact_lock(hashtextextended(p_consultation_id::text, 0));
  SELECT * INTO v_assignment
  FROM public.consultation_assignments
  WHERE consultation_id = p_consultation_id AND status = 'claimed'
  ORDER BY claimed_at DESC NULLS LAST
  LIMIT 1 FOR UPDATE;

  IF FOUND AND v_assignment.lock_expires_at > v_now AND v_assignment.doctor_id <> p_doctor_id THEN
    RETURN jsonb_build_object('success', false, 'assignment', to_jsonb(v_assignment));
  END IF;

  INSERT INTO public.consultation_assignments
    (consultation_id, doctor_id, assigned_by, status, claimed_at, lock_expires_at, locked_by_name)
  VALUES
    (p_consultation_id, p_doctor_id, p_doctor_name, 'claimed', v_now,
     v_now + make_interval(mins => greatest(1, least(p_lock_ttl_minutes, 120))), p_doctor_name)
  RETURNING * INTO v_assignment;
  RETURN jsonb_build_object('success', true, 'assignment', to_jsonb(v_assignment));
END;
$$;

REVOKE ALL ON FUNCTION public.claim_consultation_atomic(uuid, uuid, text, integer) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.claim_consultation_atomic(uuid, uuid, text, integer) TO service_role;
