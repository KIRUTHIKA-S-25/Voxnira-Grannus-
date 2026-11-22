CREATE TABLE IF NOT EXISTS public.staff_lockouts (
    email TEXT PRIMARY KEY,
    failed_count INT NOT NULL DEFAULT 0,
    lockout_until TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now())
);

CREATE TABLE IF NOT EXISTS public.emergency_sos_dispatches (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_name TEXT NOT NULL DEFAULT 'Emergency Patient',
    patient_phone TEXT,
    contact_phone TEXT,
    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION,
    accuracy DOUBLE PRECISION,
    emergency_type TEXT NOT NULL DEFAULT 'SEVERE_MEDICAL_DISTRESS',
    status TEXT NOT NULL DEFAULT 'DISPATCHED',
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now())
);

CREATE TABLE IF NOT EXISTS public.rate_limits (
    key TEXT PRIMARY KEY,
    request_count INT NOT NULL DEFAULT 1,
    window_start TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now())
);

ALTER TABLE public.staff_lockouts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.emergency_sos_dispatches ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.rate_limits ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.staff_lockouts FROM anon, authenticated;
REVOKE ALL ON public.emergency_sos_dispatches FROM anon, authenticated;
REVOKE ALL ON public.rate_limits FROM anon, authenticated;
