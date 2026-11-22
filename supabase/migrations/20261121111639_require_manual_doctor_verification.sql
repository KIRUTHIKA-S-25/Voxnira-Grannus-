ALTER TABLE public.staff_users
  ALTER COLUMN is_verified_doctor SET DEFAULT false;

UPDATE public.staff_users
SET is_verified_doctor = false
WHERE role <> 'doctor' AND is_verified_doctor IS DISTINCT FROM false;
