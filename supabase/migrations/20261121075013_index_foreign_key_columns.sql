DO $$
DECLARE
  fk record;
  idx_name text;
BEGIN
  FOR fk IN
    SELECT c.conrelid, n.nspname AS schema_name, t.relname AS table_name,
           a.attname AS column_name, a.attnum
    FROM pg_constraint c
    JOIN pg_class t ON t.oid = c.conrelid
    JOIN pg_namespace n ON n.oid = t.relnamespace
    JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
    WHERE c.contype = 'f'
      AND n.nspname = 'public'
      AND cardinality(c.conkey) = 1
      AND NOT EXISTS (
        SELECT 1 FROM pg_index i
        WHERE i.indrelid = c.conrelid AND i.indisvalid AND i.indkey[0] = a.attnum
      )
  LOOP
    idx_name := 'idx_fk_' || fk.table_name || '_' || fk.column_name;
    EXECUTE format('CREATE INDEX IF NOT EXISTS %I ON %I.%I (%I)',
                   idx_name, fk.schema_name, fk.table_name, fk.column_name);
  END LOOP;
END
$$;
