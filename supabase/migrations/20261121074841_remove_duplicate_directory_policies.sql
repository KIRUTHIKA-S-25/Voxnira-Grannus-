-- The PUBLIC policies already allow anonymous directory reads; these were duplicates.
DROP POLICY IF EXISTS "Public read hospitals" ON public.hospitals;
DROP POLICY IF EXISTS "Public read departments" ON public.departments;
