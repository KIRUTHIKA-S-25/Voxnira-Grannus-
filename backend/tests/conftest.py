"""Keep automated application tests away from configured external databases."""
import os

os.environ["ENV"] = "development"
os.environ["SUPABASE_URL"] = ""
os.environ["SUPABASE_SERVICE_ROLE_KEY"] = ""
os.environ["SUPABASE_ANON_KEY"] = ""
