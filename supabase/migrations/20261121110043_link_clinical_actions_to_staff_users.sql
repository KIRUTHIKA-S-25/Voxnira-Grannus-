ALTER TABLE public.doctor_actions
  DROP CONSTRAINT IF EXISTS doctor_actions_doctor_id_fkey;
ALTER TABLE public.doctor_actions
  ADD CONSTRAINT doctor_actions_doctor_id_fkey
  FOREIGN KEY (doctor_id) REFERENCES public.staff_users(id) ON DELETE SET NULL;
