-- SOS requests are recorded by this application; no ambulance dispatch API is integrated.
ALTER TABLE public.emergency_sos_dispatches
  ALTER COLUMN status SET DEFAULT 'RECORDED';

UPDATE public.emergency_sos_dispatches
SET status = 'RECORDED'
WHERE status = 'DISPATCHED';
