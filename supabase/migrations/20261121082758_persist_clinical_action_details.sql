ALTER TABLE public.doctor_actions
  ADD COLUMN IF NOT EXISTS action_id text,
  ADD COLUMN IF NOT EXISTS details jsonb NOT NULL DEFAULT '{}'::jsonb;

CREATE UNIQUE INDEX IF NOT EXISTS doctor_actions_action_id_key
  ON public.doctor_actions(action_id)
  WHERE action_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS doctor_actions_consultation_created_idx
  ON public.doctor_actions(consultation_id, created_at DESC);
