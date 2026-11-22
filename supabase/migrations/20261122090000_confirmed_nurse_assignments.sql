-- Persist every nurse-confirmed assignment with a stable assignment timestamp.
ALTER TABLE public.consultation_assignments
  ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now();

ALTER TABLE public.consultation_assignments
  DROP CONSTRAINT IF EXISTS consultation_assignments_status_check;

ALTER TABLE public.consultation_assignments
  ADD CONSTRAINT consultation_assignments_status_check
  CHECK (status = ANY (ARRAY['active', 'assigned', 'claimed', 'completed', 'transferred', 'released']::text[]));

CREATE INDEX IF NOT EXISTS consultation_assignments_case_created_idx
  ON public.consultation_assignments (consultation_id, created_at DESC);

-- The case workflow uses explicit follow-up and referral dispositions.
ALTER TABLE public.consultations
  DROP CONSTRAINT IF EXISTS consultations_status_check;

ALTER TABLE public.consultations
  ADD CONSTRAINT consultations_status_check
  CHECK (status = ANY (ARRAY[
    'triage', 'queue', 'in_progress', 'completed', 'cancelled',
    'prescribed', 'referred', 'follow_up_required'
  ]::text[]));
