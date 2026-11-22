CREATE OR REPLACE FUNCTION public.consume_otp_cooldown(p_key text, p_cooldown_seconds integer DEFAULT 30)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public, pg_temp
AS $function$
DECLARE
  v_window_start timestamptz;
  v_cooldown integer := greatest(1, least(p_cooldown_seconds, 3600));
  v_retry_after integer;
BEGIN
  INSERT INTO public.rate_limits(key, request_count, window_start)
  VALUES (p_key, 1, now())
  ON CONFLICT (key) DO UPDATE
    SET request_count = 1, window_start = now()
    WHERE public.rate_limits.window_start <= now() - make_interval(secs => v_cooldown)
  RETURNING window_start INTO v_window_start;

  IF FOUND THEN
    RETURN jsonb_build_object('allowed', true, 'retry_after', 0);
  END IF;

  SELECT window_start INTO v_window_start
  FROM public.rate_limits
  WHERE key = p_key;

  v_retry_after := greatest(
    1,
    ceil(extract(epoch FROM (v_window_start + make_interval(secs => v_cooldown) - now())))::integer
  );
  RETURN jsonb_build_object('allowed', false, 'retry_after', v_retry_after);
END;
$function$;

REVOKE ALL ON FUNCTION public.consume_otp_cooldown(text, integer) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.consume_otp_cooldown(text, integer) TO service_role;
