ALTER TABLE public.pipeline_jobs
  ADD COLUMN IF NOT EXISTS guest_case_id uuid REFERENCES public.guest_cases(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_pipeline_jobs_guest_case_id
  ON public.pipeline_jobs(guest_case_id);
