-- Reference snapshot of the live Supabase public schema on 2026-11-21.
-- Not a standalone migration: Supabase auth/extensions and seed data are external prerequisites.
CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA extensions;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp" WITH SCHEMA extensions;
SET search_path = public, extensions;

CREATE TABLE "profiles" (
  "id" uuid NOT NULL,
  "role" text NOT NULL,
  "full_name" text,
  "phone_number" text,
  "preferred_language" text DEFAULT 'en-IN'::text,
  "created_at" timestamp with time zone DEFAULT now(),
  "updated_at" timestamp with time zone DEFAULT now()
);
CREATE TABLE "patients" (
  "id" uuid NOT NULL,
  "age" integer,
  "gender" text,
  "known_conditions" text[],
  "current_medications" text[],
  "allergies" text[],
  "phi_ciphertext" text
);
CREATE TABLE "doctors" (
  "id" uuid NOT NULL,
  "specialization" text,
  "license_number" text,
  "is_available" boolean DEFAULT true
);
CREATE TABLE "locations" (
  "id" uuid DEFAULT extensions.uuid_generate_v4() NOT NULL,
  "patient_id" uuid,
  "latitude" numeric,
  "longitude" numeric,
  "address_text" text,
  "recorded_at" timestamp with time zone DEFAULT now(),
  "location_ciphertext" text
);
CREATE TABLE "consents" (
  "id" uuid DEFAULT extensions.uuid_generate_v4() NOT NULL,
  "patient_id" uuid,
  "consent_type" text NOT NULL,
  "is_granted" boolean NOT NULL,
  "granted_at" timestamp with time zone DEFAULT now(),
  "version" text DEFAULT '2023.1-DPDP'::text,
  "language_code" text DEFAULT 'en-IN'::text,
  "ip_hash" text,
    "profile_id" text,
    "owner_type" text,
    "owner_id" text,
    "account_id" uuid,
    "guest_case_id" uuid
);
CREATE TABLE "consultations" (
  "id" uuid DEFAULT extensions.uuid_generate_v4() NOT NULL,
  "patient_id" uuid,
  "doctor_id" uuid,
  "status" text DEFAULT 'triage'::text NOT NULL,
  "patient_language" text,
  "original_transcript" text,
  "english_transcript" text,
  "audio_url" text,
  "created_at" timestamp with time zone DEFAULT now(),
  "updated_at" timestamp with time zone DEFAULT now(),
  "hospital_id" uuid,
  "profile_id" uuid,
  "account_id" uuid,
  "urgency_tier" text DEFAULT 'doctor_today'::text,
  "department_id" uuid,
  "assigned_doctor_id" uuid,
  "complaint_category" text,
  "chief_complaint" text,
  "full_result" jsonb,
  "prescriptions" jsonb DEFAULT '[]'::jsonb NOT NULL,
  "prescribed_by" text,
  "doctor_registration_number" text,
  "prescription_timestamp" timestamp with time zone,
  "referral_hospital" text
);
CREATE TABLE "clinical_summaries" (
  "id" uuid DEFAULT extensions.uuid_generate_v4() NOT NULL,
  "consultation_id" uuid,
  "chief_complaint" text,
  "relevant_history" text,
  "extraction_notes" text,
  "doctor_translated_summary" jsonb,
  "home_remedy_guidance" jsonb,
  "created_at" timestamp with time zone DEFAULT now()
);
CREATE TABLE "symptoms" (
  "id" uuid DEFAULT extensions.uuid_generate_v4() NOT NULL,
  "consultation_id" uuid,
  "name" text NOT NULL,
  "is_negated" boolean DEFAULT false,
  "severity" text,
  "duration_value" numeric,
  "duration_unit" text,
  "body_location" text
);
CREATE TABLE "safety_assessments" (
  "id" uuid DEFAULT extensions.uuid_generate_v4() NOT NULL,
  "consultation_id" uuid,
  "has_critical_flags" boolean DEFAULT false,
  "override_priority" boolean DEFAULT false,
  "created_at" timestamp with time zone DEFAULT now()
);
CREATE TABLE "priority_assessments" (
  "id" uuid DEFAULT extensions.uuid_generate_v4() NOT NULL,
  "consultation_id" uuid,
  "level" text NOT NULL,
  "confidence" numeric,
  "model_used" text,
  "reasons" text[],
  "created_at" timestamp with time zone DEFAULT now()
);
CREATE TABLE "follow_up_questions" (
  "id" uuid DEFAULT extensions.uuid_generate_v4() NOT NULL,
  "consultation_id" uuid,
  "english_question" text NOT NULL,
  "translated_question" text,
  "is_answered" boolean DEFAULT false,
  "patient_answer" text,
  "created_at" timestamp with time zone DEFAULT now()
);
CREATE TABLE "doctor_actions" (
  "id" uuid DEFAULT extensions.uuid_generate_v4() NOT NULL,
  "consultation_id" uuid,
  "doctor_id" uuid,
  "action_type" text NOT NULL,
  "notes" text,
  "created_at" timestamp with time zone DEFAULT now(),
  "action_id" text,
  "details" jsonb DEFAULT '{}'::jsonb NOT NULL,
  "idempotency_fingerprint" text
);
CREATE TABLE "pipeline_results" (
  "id" uuid DEFAULT extensions.uuid_generate_v4() NOT NULL,
  "consultation_id" uuid,
  "request_id" text NOT NULL,
  "full_result" jsonb NOT NULL,
  "priority_level" text NOT NULL,
  "chief_complaint" text,
  "patient_language" text,
  "has_red_flags" boolean DEFAULT false,
  "confidence" numeric,
  "created_at" timestamp with time zone DEFAULT now()
);
CREATE TABLE "hospitals" (
  "id" uuid DEFAULT gen_random_uuid() NOT NULL,
  "name" text NOT NULL,
  "code" text NOT NULL,
  "location" text,
  "emergency_phone" text DEFAULT '108'::text,
  "created_at" timestamp with time zone DEFAULT now()
);
CREATE TABLE "departments" (
  "id" uuid DEFAULT gen_random_uuid() NOT NULL,
  "hospital_id" uuid,
  "name" text NOT NULL,
  "code" text NOT NULL,
  "created_at" timestamp with time zone DEFAULT now()
);
CREATE TABLE "accounts" (
  "id" uuid DEFAULT gen_random_uuid() NOT NULL,
  "phone_number" text NOT NULL,
  "phone_lookup_hash" text,
  "pin_hash" text,
  "failed_pin_attempts" integer DEFAULT 0 NOT NULL,
  "locked_until" timestamp with time zone,
  "is_active" boolean DEFAULT true NOT NULL,
  "is_verified" boolean DEFAULT false,
  "created_at" timestamp with time zone DEFAULT now(),
  "updated_at" timestamp with time zone DEFAULT now()
);
CREATE TABLE "otp_verifications" (
  "id" uuid DEFAULT gen_random_uuid() NOT NULL,
  "phone_number" text NOT NULL,
  "phone_lookup_hash" text,
  "otp_hash" text NOT NULL,
  "attempts" integer DEFAULT 0,
  "max_attempts" integer DEFAULT 5,
  "expires_at" timestamp with time zone NOT NULL,
  "is_used" boolean DEFAULT false,
  "created_at" timestamp with time zone DEFAULT now()
);
CREATE TABLE "patient_profiles" (
  "id" uuid DEFAULT gen_random_uuid() NOT NULL,
  "account_id" uuid,
  "hospital_id" uuid,
  "full_name" text NOT NULL,
  "age" text,
  "gender" text,
  "guardian_name" text,
  "relation" text DEFAULT 'self'::text,
  "preferred_language" text DEFAULT 'ta-IN'::text,
  "allergies" text[] DEFAULT '{}'::text[],
  "medications" text[] DEFAULT '{}'::text[],
  "known_conditions" text[] DEFAULT '{}'::text[],
  "pin_hash" text,
  "is_caregiver_managed" boolean DEFAULT false NOT NULL,
  "caregiver_relationship" text,
  "created_at" timestamp with time zone DEFAULT now(),
  "updated_at" timestamp with time zone DEFAULT now()
);
CREATE TABLE "staff_users" (
  "id" uuid DEFAULT gen_random_uuid() NOT NULL,
  "hospital_id" uuid,
  "department_id" uuid,
  "email" text NOT NULL,
  "password_hash" text NOT NULL,
  "role" text NOT NULL,
  "full_name" text NOT NULL,
  "doctor_registration_number" text,
  "state_council" text,
  "specialty" text,
  "languages" text[] DEFAULT ARRAY['en'::text, 'ta'::text],
  "is_active" boolean DEFAULT true,
  "is_on_duty" boolean DEFAULT true,
  "active_case_load" integer DEFAULT 0,
  "created_at" timestamp with time zone DEFAULT now(),
  "updated_at" timestamp with time zone DEFAULT now(),
  "max_capacity" integer DEFAULT 15,
  "weekly_schedule" jsonb DEFAULT '{}'::jsonb,
  "is_verified_doctor" boolean DEFAULT false
);
CREATE TABLE "pipeline_jobs" (
  "id" text NOT NULL,
  "hospital_id" uuid,
  "account_id" uuid,
  "profile_id" uuid,
  "guest_case_id" uuid,
  "status" text NOT NULL,
  "stage" text NOT NULL,
  "progress" integer DEFAULT 0,
  "message" text,
  "result" jsonb,
  "error_message" text,
  "created_at" timestamp with time zone DEFAULT now(),
  "updated_at" timestamp with time zone DEFAULT now()
);
CREATE TABLE "persistent_voice_threads" (
  "id" text NOT NULL,
  "consultation_id" uuid,
  "hospital_id" uuid,
  "patient_profile_id" uuid,
  "doctor_id" uuid,
  "status" text DEFAULT 'open'::text,
  "messages" jsonb DEFAULT '[]'::jsonb,
  "created_at" timestamp with time zone DEFAULT now(),
  "updated_at" timestamp with time zone DEFAULT now()
);
CREATE TABLE "persistent_follow_ups" (
  "id" text NOT NULL,
  "consultation_id" uuid,
  "hospital_id" uuid,
  "profile_id" uuid,
  "urgency_tier" text NOT NULL,
  "scheduled_for" timestamp with time zone NOT NULL,
  "status" text DEFAULT 'PENDING'::text,
  "patient_status" text,
  "patient_feedback" text,
  "checked_in_at" timestamp with time zone,
  "created_at" timestamp with time zone DEFAULT now()
);
CREATE TABLE "consultation_assignments" (
  "id" uuid DEFAULT gen_random_uuid() NOT NULL,
  "consultation_id" uuid,
  "doctor_id" uuid,
  "assigned_by" text DEFAULT 'system'::text,
  "status" text DEFAULT 'active'::text,
  "reassignment_reason" text,
  "claimed_at" timestamp with time zone DEFAULT now(),
  "created_at" timestamp with time zone DEFAULT now() NOT NULL,
  "released_at" timestamp with time zone,
  "lock_expires_at" timestamp with time zone,
  "locked_by_name" text
);
CREATE TABLE "audit_logs" (
  "id" uuid DEFAULT gen_random_uuid() NOT NULL,
  "hospital_id" uuid,
  "action" text NOT NULL,
  "user_id" text NOT NULL,
  "role" text NOT NULL,
  "resource_id" text,
  "details" jsonb DEFAULT '{}'::jsonb,
  "ip_address" text,
  "created_at" timestamp with time zone DEFAULT now()
);
CREATE TABLE "guest_cases" (
  "id" uuid DEFAULT gen_random_uuid() NOT NULL,
  "hospital_id" uuid,
  "guest_token_hash" text NOT NULL,
  "patient_name" text NOT NULL,
  "age" integer,
  "urgency_tier" text DEFAULT 'emergency'::text NOT NULL,
  "chief_complaint" text,
  "status" text DEFAULT 'unverified'::text NOT NULL,
  "created_at" timestamp with time zone DEFAULT now() NOT NULL,
  "verified_at" timestamp with time zone,
  "account_id" uuid,
  "age_ciphertext" text,
  "location_ciphertext" text
);
CREATE TABLE "deletion_log" (
  "id" uuid DEFAULT gen_random_uuid() NOT NULL,
  "hospital_id" uuid,
  "resource_type" text NOT NULL,
  "resource_id" text NOT NULL,
  "reason" text NOT NULL,
  "deleted_at" timestamp with time zone DEFAULT now() NOT NULL,
  "metadata" jsonb DEFAULT '{}'::jsonb
);
CREATE TABLE "audio_retention_queue" (
  "id" uuid DEFAULT gen_random_uuid() NOT NULL,
  "file_path" text NOT NULL,
  "hospital_id" uuid,
  "created_at" timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
  "delete_after" timestamp with time zone NOT NULL,
  "purged" boolean DEFAULT false NOT NULL,
  "purged_at" timestamp with time zone,
  "metadata" jsonb DEFAULT '{}'::jsonb,
  "file_path_lookup_hash" text
);
CREATE TABLE "revoked_tokens" (
  "token_jti" text NOT NULL,
  "user_id" text NOT NULL,
  "revoked_at" timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
  "expires_at" timestamp with time zone NOT NULL
);
CREATE TABLE "staff_lockouts" (
  "email" text NOT NULL,
  "failed_count" integer DEFAULT 0 NOT NULL,
  "lockout_until" timestamp with time zone,
  "updated_at" timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL
);
CREATE TABLE "emergency_sos_dispatches" (
  "id" uuid DEFAULT gen_random_uuid() NOT NULL,
  "patient_name" text DEFAULT 'Emergency Patient'::text NOT NULL,
  "patient_phone" text,
  "contact_phone" text,
  "latitude" double precision,
  "longitude" double precision,
  "accuracy" double precision,
  "emergency_type" text DEFAULT 'SEVERE_MEDICAL_DISTRESS'::text NOT NULL,
  "status" text DEFAULT 'RECORDED'::text NOT NULL,
  "notes" text,
  "created_at" timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
  "location_ciphertext" text
);
CREATE TABLE "rate_limits" (
  "key" text NOT NULL,
  "request_count" integer DEFAULT 1 NOT NULL,
  "window_start" timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL
);

ALTER TABLE accounts ADD CONSTRAINT "accounts_phone_number_key" UNIQUE (phone_number);
ALTER TABLE accounts ADD CONSTRAINT "accounts_pkey" PRIMARY KEY (id);
ALTER TABLE audio_retention_queue ADD CONSTRAINT "audio_retention_queue_file_path_key" UNIQUE (file_path);
ALTER TABLE audio_retention_queue ADD CONSTRAINT "audio_retention_queue_hospital_id_fkey" FOREIGN KEY (hospital_id) REFERENCES hospitals(id) ON DELETE SET NULL;
ALTER TABLE audio_retention_queue ADD CONSTRAINT "audio_retention_queue_pkey" PRIMARY KEY (id);
ALTER TABLE audit_logs ADD CONSTRAINT "audit_logs_hospital_id_fkey" FOREIGN KEY (hospital_id) REFERENCES hospitals(id);
ALTER TABLE audit_logs ADD CONSTRAINT "audit_logs_pkey" PRIMARY KEY (id);
ALTER TABLE clinical_summaries ADD CONSTRAINT "clinical_summaries_consultation_id_fkey" FOREIGN KEY (consultation_id) REFERENCES consultations(id) ON DELETE CASCADE;
ALTER TABLE clinical_summaries ADD CONSTRAINT "clinical_summaries_consultation_id_key" UNIQUE (consultation_id);
ALTER TABLE clinical_summaries ADD CONSTRAINT "clinical_summaries_pkey" PRIMARY KEY (id);
ALTER TABLE consents ADD CONSTRAINT "consents_patient_id_fkey" FOREIGN KEY (patient_id) REFERENCES patients(id) ON DELETE CASCADE;
ALTER TABLE consents ADD CONSTRAINT "consents_account_id_fkey" FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE SET NULL;
ALTER TABLE consents ADD CONSTRAINT "consents_guest_case_id_fkey" FOREIGN KEY (guest_case_id) REFERENCES guest_cases(id) ON DELETE SET NULL;
ALTER TABLE consents ADD CONSTRAINT "consents_owner_type_check" CHECK (owner_type IS NULL OR owner_type = ANY (ARRAY['patient_account'::text, 'guest_case'::text]));
ALTER TABLE consents ADD CONSTRAINT "consents_owner_link_check" CHECK ((owner_type IS NULL AND owner_id IS NULL) OR (owner_type = 'patient_account'::text AND owner_id IS NOT NULL AND account_id IS NOT NULL AND guest_case_id IS NULL) OR (owner_type = 'guest_case'::text AND owner_id IS NOT NULL AND guest_case_id IS NOT NULL AND account_id IS NULL));
ALTER TABLE consents ADD CONSTRAINT "consents_pkey" PRIMARY KEY (id);
ALTER TABLE consultation_assignments ADD CONSTRAINT "consultation_assignments_consultation_id_fkey" FOREIGN KEY (consultation_id) REFERENCES consultations(id) ON DELETE CASCADE;
ALTER TABLE consultation_assignments ADD CONSTRAINT "consultation_assignments_doctor_id_fkey" FOREIGN KEY (doctor_id) REFERENCES staff_users(id) ON DELETE CASCADE;
ALTER TABLE consultation_assignments ADD CONSTRAINT "consultation_assignments_pkey" PRIMARY KEY (id);
ALTER TABLE consultation_assignments ADD CONSTRAINT "consultation_assignments_status_check" CHECK (status = ANY (ARRAY['active'::text, 'assigned'::text, 'claimed'::text, 'completed'::text, 'transferred'::text, 'released'::text]));
ALTER TABLE consultations ADD CONSTRAINT "consultations_account_id_fkey" FOREIGN KEY (account_id) REFERENCES accounts(id);
ALTER TABLE consultations ADD CONSTRAINT "consultations_assigned_doctor_id_fkey" FOREIGN KEY (assigned_doctor_id) REFERENCES staff_users(id);
ALTER TABLE consultations ADD CONSTRAINT "consultations_department_id_fkey" FOREIGN KEY (department_id) REFERENCES departments(id);
ALTER TABLE consultations ADD CONSTRAINT "consultations_doctor_id_fkey" FOREIGN KEY (doctor_id) REFERENCES doctors(id) ON DELETE SET NULL;
ALTER TABLE consultations ADD CONSTRAINT "consultations_hospital_id_fkey" FOREIGN KEY (hospital_id) REFERENCES hospitals(id);
ALTER TABLE consultations ADD CONSTRAINT "consultations_patient_id_fkey" FOREIGN KEY (patient_id) REFERENCES patients(id) ON DELETE CASCADE;
ALTER TABLE consultations ADD CONSTRAINT "consultations_pkey" PRIMARY KEY (id);
ALTER TABLE consultations ADD CONSTRAINT "consultations_profile_id_fkey" FOREIGN KEY (profile_id) REFERENCES patient_profiles(id);
ALTER TABLE consultations ADD CONSTRAINT "consultations_status_check" CHECK (status = ANY (ARRAY['triage'::text, 'queue'::text, 'in_progress'::text, 'completed'::text, 'cancelled'::text, 'prescribed'::text, 'referred'::text, 'follow_up_required'::text]));
ALTER TABLE deletion_log ADD CONSTRAINT "deletion_log_hospital_id_fkey" FOREIGN KEY (hospital_id) REFERENCES hospitals(id) ON DELETE SET NULL;
ALTER TABLE deletion_log ADD CONSTRAINT "deletion_log_pkey" PRIMARY KEY (id);
ALTER TABLE departments ADD CONSTRAINT "departments_hospital_id_code_key" UNIQUE (hospital_id, code);
ALTER TABLE departments ADD CONSTRAINT "departments_hospital_id_fkey" FOREIGN KEY (hospital_id) REFERENCES hospitals(id) ON DELETE CASCADE;
ALTER TABLE departments ADD CONSTRAINT "departments_pkey" PRIMARY KEY (id);
ALTER TABLE doctor_actions ADD CONSTRAINT "doctor_actions_consultation_id_fkey" FOREIGN KEY (consultation_id) REFERENCES consultations(id) ON DELETE CASCADE;
ALTER TABLE doctor_actions ADD CONSTRAINT "doctor_actions_doctor_id_fkey" FOREIGN KEY (doctor_id) REFERENCES staff_users(id) ON DELETE SET NULL;
ALTER TABLE doctor_actions ADD CONSTRAINT "doctor_actions_pkey" PRIMARY KEY (id);
ALTER TABLE doctors ADD CONSTRAINT "doctors_id_fkey" FOREIGN KEY (id) REFERENCES profiles(id) ON DELETE CASCADE;
ALTER TABLE doctors ADD CONSTRAINT "doctors_license_number_key" UNIQUE (license_number);
ALTER TABLE doctors ADD CONSTRAINT "doctors_pkey" PRIMARY KEY (id);
ALTER TABLE emergency_sos_dispatches ADD CONSTRAINT "emergency_sos_dispatches_pkey" PRIMARY KEY (id);
ALTER TABLE follow_up_questions ADD CONSTRAINT "follow_up_questions_consultation_id_fkey" FOREIGN KEY (consultation_id) REFERENCES consultations(id) ON DELETE CASCADE;
ALTER TABLE follow_up_questions ADD CONSTRAINT "follow_up_questions_pkey" PRIMARY KEY (id);
ALTER TABLE guest_cases ADD CONSTRAINT "guest_cases_account_id_fkey" FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE SET NULL;
ALTER TABLE guest_cases ADD CONSTRAINT "guest_cases_hospital_id_fkey" FOREIGN KEY (hospital_id) REFERENCES hospitals(id) ON DELETE SET NULL;
ALTER TABLE guest_cases ADD CONSTRAINT "guest_cases_pkey" PRIMARY KEY (id);
ALTER TABLE hospitals ADD CONSTRAINT "hospitals_code_key" UNIQUE (code);
ALTER TABLE hospitals ADD CONSTRAINT "hospitals_pkey" PRIMARY KEY (id);
ALTER TABLE locations ADD CONSTRAINT "locations_patient_id_fkey" FOREIGN KEY (patient_id) REFERENCES patients(id) ON DELETE CASCADE;
ALTER TABLE locations ADD CONSTRAINT "locations_pkey" PRIMARY KEY (id);
ALTER TABLE otp_verifications ADD CONSTRAINT "otp_verifications_pkey" PRIMARY KEY (id);
ALTER TABLE patient_profiles ADD CONSTRAINT "patient_profiles_account_id_fkey" FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE;
ALTER TABLE patient_profiles ADD CONSTRAINT "patient_profiles_hospital_id_fkey" FOREIGN KEY (hospital_id) REFERENCES hospitals(id);
ALTER TABLE patient_profiles ADD CONSTRAINT "patient_profiles_pkey" PRIMARY KEY (id);
ALTER TABLE patients ADD CONSTRAINT "patients_id_fkey" FOREIGN KEY (id) REFERENCES profiles(id) ON DELETE CASCADE;
ALTER TABLE patients ADD CONSTRAINT "patients_pkey" PRIMARY KEY (id);
ALTER TABLE persistent_follow_ups ADD CONSTRAINT "persistent_follow_ups_consultation_id_fkey" FOREIGN KEY (consultation_id) REFERENCES consultations(id) ON DELETE CASCADE;
ALTER TABLE persistent_follow_ups ADD CONSTRAINT "persistent_follow_ups_hospital_id_fkey" FOREIGN KEY (hospital_id) REFERENCES hospitals(id);
ALTER TABLE persistent_follow_ups ADD CONSTRAINT "persistent_follow_ups_pkey" PRIMARY KEY (id);
ALTER TABLE persistent_follow_ups ADD CONSTRAINT "persistent_follow_ups_profile_id_fkey" FOREIGN KEY (profile_id) REFERENCES patient_profiles(id);
ALTER TABLE persistent_follow_ups ADD CONSTRAINT "persistent_follow_ups_status_check" CHECK (status = ANY (ARRAY['PENDING'::text, 'SENT'::text, 'COMPLETED'::text, 'ESCALATED'::text, 'EXPIRED'::text]));
ALTER TABLE persistent_voice_threads ADD CONSTRAINT "persistent_voice_threads_consultation_id_fkey" FOREIGN KEY (consultation_id) REFERENCES consultations(id) ON DELETE CASCADE;
ALTER TABLE persistent_voice_threads ADD CONSTRAINT "persistent_voice_threads_doctor_id_fkey" FOREIGN KEY (doctor_id) REFERENCES staff_users(id);
ALTER TABLE persistent_voice_threads ADD CONSTRAINT "persistent_voice_threads_hospital_id_fkey" FOREIGN KEY (hospital_id) REFERENCES hospitals(id);
ALTER TABLE persistent_voice_threads ADD CONSTRAINT "persistent_voice_threads_patient_profile_id_fkey" FOREIGN KEY (patient_profile_id) REFERENCES patient_profiles(id);
ALTER TABLE persistent_voice_threads ADD CONSTRAINT "persistent_voice_threads_pkey" PRIMARY KEY (id);
ALTER TABLE persistent_voice_threads ADD CONSTRAINT "persistent_voice_threads_status_check" CHECK (status = ANY (ARRAY['open'::text, 'doctor_replied'::text, 'patient_replied'::text, 'closed'::text]));
ALTER TABLE pipeline_jobs ADD CONSTRAINT "pipeline_jobs_account_id_fkey" FOREIGN KEY (account_id) REFERENCES accounts(id);
ALTER TABLE pipeline_jobs ADD CONSTRAINT "pipeline_jobs_hospital_id_fkey" FOREIGN KEY (hospital_id) REFERENCES hospitals(id);
ALTER TABLE pipeline_jobs ADD CONSTRAINT "pipeline_jobs_pkey" PRIMARY KEY (id);
ALTER TABLE pipeline_jobs ADD CONSTRAINT "pipeline_jobs_guest_case_id_fkey" FOREIGN KEY (guest_case_id) REFERENCES guest_cases(id) ON DELETE SET NULL;
ALTER TABLE pipeline_jobs ADD CONSTRAINT "pipeline_jobs_profile_id_fkey" FOREIGN KEY (profile_id) REFERENCES patient_profiles(id);
ALTER TABLE pipeline_jobs ADD CONSTRAINT "pipeline_jobs_status_check" CHECK (status = ANY (ARRAY['QUEUED'::text, 'PREPROCESSING'::text, 'TRANSCRIBING'::text, 'EXTRACTING'::text, 'SAFETY_SCREENING'::text, 'TRIAGING'::text, 'COMPLETED'::text, 'FAILED'::text]));
ALTER TABLE pipeline_results ADD CONSTRAINT "pipeline_results_consultation_id_fkey" FOREIGN KEY (consultation_id) REFERENCES consultations(id) ON DELETE CASCADE;
ALTER TABLE pipeline_results ADD CONSTRAINT "pipeline_results_consultation_id_key" UNIQUE (consultation_id);
ALTER TABLE pipeline_results ADD CONSTRAINT "pipeline_results_pkey" PRIMARY KEY (id);
ALTER TABLE pipeline_results ADD CONSTRAINT "pipeline_results_priority_level_check" CHECK (priority_level = ANY (ARRAY['LOW'::text, 'MEDIUM'::text, 'HIGH'::text, 'PENDING_REVIEW'::text]));
ALTER TABLE priority_assessments ADD CONSTRAINT "priority_assessments_consultation_id_fkey" FOREIGN KEY (consultation_id) REFERENCES consultations(id) ON DELETE CASCADE;
ALTER TABLE priority_assessments ADD CONSTRAINT "priority_assessments_consultation_id_key" UNIQUE (consultation_id);
ALTER TABLE priority_assessments ADD CONSTRAINT "priority_assessments_level_check" CHECK (level = ANY (ARRAY['LOW'::text, 'MEDIUM'::text, 'HIGH'::text]));
ALTER TABLE priority_assessments ADD CONSTRAINT "priority_assessments_pkey" PRIMARY KEY (id);
ALTER TABLE profiles ADD CONSTRAINT "profiles_id_fkey" FOREIGN KEY (id) REFERENCES auth.users(id) ON DELETE CASCADE;
ALTER TABLE profiles ADD CONSTRAINT "profiles_pkey" PRIMARY KEY (id);
ALTER TABLE profiles ADD CONSTRAINT "profiles_role_check" CHECK (role = ANY (ARRAY['patient'::text, 'doctor'::text, 'admin'::text]));
ALTER TABLE rate_limits ADD CONSTRAINT "rate_limits_pkey" PRIMARY KEY (key);
ALTER TABLE revoked_tokens ADD CONSTRAINT "revoked_tokens_pkey" PRIMARY KEY (token_jti);
ALTER TABLE safety_assessments ADD CONSTRAINT "safety_assessments_consultation_id_fkey" FOREIGN KEY (consultation_id) REFERENCES consultations(id) ON DELETE CASCADE;
ALTER TABLE safety_assessments ADD CONSTRAINT "safety_assessments_consultation_id_key" UNIQUE (consultation_id);
ALTER TABLE safety_assessments ADD CONSTRAINT "safety_assessments_pkey" PRIMARY KEY (id);
ALTER TABLE staff_lockouts ADD CONSTRAINT "staff_lockouts_pkey" PRIMARY KEY (email);
ALTER TABLE staff_users ADD CONSTRAINT "staff_users_department_id_fkey" FOREIGN KEY (department_id) REFERENCES departments(id);
ALTER TABLE staff_users ADD CONSTRAINT "staff_users_email_key" UNIQUE (email);
ALTER TABLE staff_users ADD CONSTRAINT "staff_users_hospital_id_fkey" FOREIGN KEY (hospital_id) REFERENCES hospitals(id);
ALTER TABLE staff_users ADD CONSTRAINT "staff_users_pkey" PRIMARY KEY (id);
ALTER TABLE staff_users ADD CONSTRAINT "staff_users_role_check" CHECK (role = ANY (ARRAY['doctor'::text, 'nurse'::text, 'admin'::text]));
ALTER TABLE symptoms ADD CONSTRAINT "symptoms_consultation_id_fkey" FOREIGN KEY (consultation_id) REFERENCES consultations(id) ON DELETE CASCADE;
ALTER TABLE symptoms ADD CONSTRAINT "symptoms_pkey" PRIMARY KEY (id);

CREATE INDEX idx_fk_audio_retention_queue_hospital_id ON public.audio_retention_queue USING btree (hospital_id);
CREATE UNIQUE INDEX audio_retention_queue_file_path_lookup_hash_key ON public.audio_retention_queue USING btree (file_path_lookup_hash);
CREATE INDEX idx_fk_audit_logs_hospital_id ON public.audit_logs USING btree (hospital_id);
CREATE INDEX idx_fk_consents_patient_id ON public.consents USING btree (patient_id);
CREATE INDEX idx_consents_owner_version ON public.consents USING btree (owner_type, owner_id, version, granted_at DESC);
CREATE INDEX idx_assignments_doc ON public.consultation_assignments USING btree (doctor_id, status);
CREATE INDEX idx_fk_consultation_assignments_consultation_id ON public.consultation_assignments USING btree (consultation_id);
CREATE INDEX idx_fk_consultations_account_id ON public.consultations USING btree (account_id);
CREATE INDEX idx_fk_consultations_assigned_doctor_id ON public.consultations USING btree (assigned_doctor_id);
CREATE INDEX idx_fk_consultations_department_id ON public.consultations USING btree (department_id);
CREATE INDEX idx_fk_consultations_doctor_id ON public.consultations USING btree (doctor_id);
CREATE INDEX idx_fk_consultations_hospital_id ON public.consultations USING btree (hospital_id);
CREATE INDEX idx_fk_consultations_patient_id ON public.consultations USING btree (patient_id);
CREATE INDEX idx_fk_consultations_profile_id ON public.consultations USING btree (profile_id);
CREATE INDEX idx_fk_deletion_log_hospital_id ON public.deletion_log USING btree (hospital_id);
CREATE INDEX idx_fk_doctor_actions_consultation_id ON public.doctor_actions USING btree (consultation_id);
CREATE INDEX idx_fk_doctor_actions_doctor_id ON public.doctor_actions USING btree (doctor_id);
CREATE UNIQUE INDEX doctor_actions_action_id_key ON public.doctor_actions USING btree (action_id) WHERE (action_id IS NOT NULL);
CREATE INDEX doctor_actions_consultation_created_idx ON public.doctor_actions USING btree (consultation_id, created_at DESC);
CREATE INDEX doctor_actions_fingerprint_created_idx ON public.doctor_actions USING btree (consultation_id, idempotency_fingerprint, created_at DESC) WHERE (idempotency_fingerprint IS NOT NULL);
CREATE INDEX idx_fk_follow_up_questions_consultation_id ON public.follow_up_questions USING btree (consultation_id);
CREATE INDEX idx_fk_guest_cases_account_id ON public.guest_cases USING btree (account_id);
CREATE INDEX idx_fk_guest_cases_hospital_id ON public.guest_cases USING btree (hospital_id);
CREATE INDEX idx_fk_locations_patient_id ON public.locations USING btree (patient_id);
CREATE INDEX idx_otp_phone ON public.otp_verifications USING btree (phone_number, created_at DESC);
CREATE UNIQUE INDEX accounts_phone_lookup_hash_key ON public.accounts USING btree (phone_lookup_hash) WHERE phone_lookup_hash IS NOT NULL;
CREATE INDEX otp_verifications_phone_lookup_hash_created_idx ON public.otp_verifications USING btree (phone_lookup_hash, created_at DESC) WHERE is_used = false;
CREATE INDEX idx_fk_patient_profiles_hospital_id ON public.patient_profiles USING btree (hospital_id);
CREATE INDEX idx_profiles_account ON public.patient_profiles USING btree (account_id);
CREATE INDEX idx_fk_persistent_follow_ups_consultation_id ON public.persistent_follow_ups USING btree (consultation_id);
CREATE INDEX idx_fk_persistent_follow_ups_hospital_id ON public.persistent_follow_ups USING btree (hospital_id);
CREATE INDEX idx_fk_persistent_follow_ups_profile_id ON public.persistent_follow_ups USING btree (profile_id);
CREATE INDEX idx_follow_ups_sched ON public.persistent_follow_ups USING btree (scheduled_for, status);
CREATE INDEX idx_fk_persistent_voice_threads_consultation_id ON public.persistent_voice_threads USING btree (consultation_id);
CREATE INDEX idx_fk_persistent_voice_threads_doctor_id ON public.persistent_voice_threads USING btree (doctor_id);
CREATE INDEX idx_fk_persistent_voice_threads_hospital_id ON public.persistent_voice_threads USING btree (hospital_id);
CREATE INDEX idx_fk_persistent_voice_threads_patient_profile_id ON public.persistent_voice_threads USING btree (patient_profile_id);
CREATE INDEX idx_fk_pipeline_jobs_account_id ON public.pipeline_jobs USING btree (account_id);
CREATE INDEX idx_fk_pipeline_jobs_hospital_id ON public.pipeline_jobs USING btree (hospital_id);
CREATE INDEX idx_fk_pipeline_jobs_profile_id ON public.pipeline_jobs USING btree (profile_id);
CREATE INDEX idx_pipeline_jobs_guest_case_id ON public.pipeline_jobs USING btree (guest_case_id);
CREATE INDEX idx_jobs_status ON public.pipeline_jobs USING btree (status, created_at DESC);
CREATE INDEX idx_pipeline_results_created ON public.pipeline_results USING btree (created_at DESC);
CREATE INDEX idx_pipeline_results_priority ON public.pipeline_results USING btree (priority_level);
CREATE INDEX idx_fk_staff_users_department_id ON public.staff_users USING btree (department_id);
CREATE INDEX idx_fk_staff_users_hospital_id ON public.staff_users USING btree (hospital_id);
CREATE INDEX idx_fk_symptoms_consultation_id ON public.symptoms USING btree (consultation_id);

ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.patients ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.doctors ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.locations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.consents ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.consultations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.clinical_summaries ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.symptoms ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.safety_assessments ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.priority_assessments ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.follow_up_questions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.doctor_actions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.pipeline_results ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.hospitals ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.departments ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.otp_verifications ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.patient_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.staff_users ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.pipeline_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.persistent_voice_threads ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.persistent_follow_ups ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.consultation_assignments ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.audit_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.guest_cases ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.deletion_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.audio_retention_queue ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.revoked_tokens ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.staff_lockouts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.emergency_sos_dispatches ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.rate_limits ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON ALL TABLES IN SCHEMA public FROM anon, authenticated;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM anon, authenticated;
GRANT ALL ON ALL TABLES IN SCHEMA public TO service_role;
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO service_role;
GRANT SELECT ON public.hospitals, public.departments TO anon;
CREATE POLICY "Public read departments" ON "public"."departments" AS PERMISSIVE FOR SELECT TO anon USING (true);
CREATE POLICY "Public read hospitals" ON "public"."hospitals" AS PERMISSIVE FOR SELECT TO anon USING (true);

CREATE OR REPLACE FUNCTION public.claim_consultation_atomic(p_consultation_id uuid, p_doctor_id uuid, p_doctor_name text, p_lock_ttl_minutes integer DEFAULT 15)
 RETURNS jsonb
 LANGUAGE plpgsql
 SET search_path TO 'public', 'pg_temp'
AS $function$ DECLARE v_assignment public.consultation_assignments%ROWTYPE; v_now timestamptz:=now(); BEGIN PERFORM pg_advisory_xact_lock(hashtextextended(p_consultation_id::text,0)); SELECT * INTO v_assignment FROM public.consultation_assignments WHERE consultation_id=p_consultation_id AND status='claimed' ORDER BY claimed_at DESC NULLS LAST LIMIT 1 FOR UPDATE; IF FOUND AND v_assignment.lock_expires_at>v_now AND v_assignment.doctor_id<>p_doctor_id THEN RETURN jsonb_build_object('success',false,'assignment',to_jsonb(v_assignment)); END IF; INSERT INTO public.consultation_assignments(consultation_id,doctor_id,assigned_by,status,claimed_at,lock_expires_at,locked_by_name) VALUES(p_consultation_id,p_doctor_id,p_doctor_name,'claimed',v_now,v_now+make_interval(mins=>greatest(1,least(p_lock_ttl_minutes,120))),p_doctor_name) RETURNING * INTO v_assignment; RETURN jsonb_build_object('success',true,'assignment',to_jsonb(v_assignment)); END; $function$;
CREATE OR REPLACE FUNCTION public.prevent_audit_log_mutation()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'public', 'pg_temp'
AS $function$
BEGIN
    RAISE EXCEPTION 'Audit log entries are immutable and cannot be updated or deleted.';
END;
$function$;

CREATE TRIGGER trg_prevent_audit_log_mutation BEFORE DELETE OR UPDATE ON audit_logs FOR EACH ROW EXECUTE FUNCTION prevent_audit_log_mutation();

REVOKE ALL ON FUNCTION public.claim_consultation_atomic(uuid, uuid, text, integer) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.claim_consultation_atomic(uuid, uuid, text, integer) TO service_role;

CREATE OR REPLACE FUNCTION public.consume_otp_cooldown(p_key text, p_cooldown_seconds integer DEFAULT 30)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY INVOKER
 SET search_path TO 'public', 'pg_temp'
AS $function$
DECLARE
  v_window_start timestamptz;
  v_cooldown integer := greatest(1, least(p_cooldown_seconds, 3600));
  v_retry_after integer;
BEGIN
  INSERT INTO public.rate_limits(key, request_count, window_start)
  VALUES (p_key, 1, now())
  ON CONFLICT (key) DO UPDATE
    SET request_count = 1, window_start = now()
    WHERE public.rate_limits.window_start <= now() - make_interval(secs => v_cooldown)
  RETURNING window_start INTO v_window_start;

  IF FOUND THEN
    RETURN jsonb_build_object('allowed', true, 'retry_after', 0);
  END IF;

  SELECT window_start INTO v_window_start FROM public.rate_limits WHERE key = p_key;
  v_retry_after := greatest(1, ceil(extract(epoch FROM (v_window_start + make_interval(secs => v_cooldown) - now())))::integer);
  RETURN jsonb_build_object('allowed', false, 'retry_after', v_retry_after);
END;
$function$;

REVOKE ALL ON FUNCTION public.consume_otp_cooldown(text, integer) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.consume_otp_cooldown(text, integer) TO service_role;

CREATE OR REPLACE FUNCTION public.erase_patient_account_atomic(p_account_id uuid, p_anonymized_phone text)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY INVOKER
 SET search_path TO 'public', 'pg_temp'
AS $function$
DECLARE
  v_profile_ids uuid[];
  v_phone_lookup_hash text;
  v_profile_count integer := 0;
BEGIN
  PERFORM pg_advisory_xact_lock(hashtextextended(p_account_id::text, 0));
  SELECT phone_lookup_hash INTO v_phone_lookup_hash
  FROM public.accounts WHERE id = p_account_id FOR UPDATE;
  IF NOT FOUND THEN
    RETURN jsonb_build_object('success', false, 'profiles_anonymized', 0);
  END IF;

  SELECT coalesce(array_agg(id), ARRAY[]::uuid[]) INTO v_profile_ids
  FROM public.patient_profiles WHERE account_id = p_account_id;
  v_profile_count := coalesce(array_length(v_profile_ids, 1), 0);

  UPDATE public.patient_profiles
  SET full_name = 'ANONYMIZED_PATIENT', age = NULL, gender = NULL,
      guardian_name = NULL, allergies = ARRAY[]::text[], medications = ARRAY[]::text[],
      known_conditions = ARRAY[]::text[], pin_hash = NULL, caregiver_relationship = NULL,
      account_id = NULL, updated_at = now()
  WHERE id = ANY(v_profile_ids);
  UPDATE public.consultations
  SET account_id = NULL, profile_id = NULL, updated_at = now()
  WHERE account_id = p_account_id OR profile_id = ANY(v_profile_ids);
  DELETE FROM public.persistent_voice_threads WHERE patient_profile_id = ANY(v_profile_ids);
  UPDATE public.persistent_follow_ups
  SET profile_id = NULL, patient_status = NULL, patient_feedback = NULL
  WHERE profile_id = ANY(v_profile_ids);
  UPDATE public.consents
  SET profile_id = NULL, account_id = NULL, owner_id = NULL, owner_type = NULL
  WHERE account_id = p_account_id
     OR profile_id IN (SELECT unnest(v_profile_ids)::text);
  UPDATE public.guest_cases SET account_id = NULL WHERE account_id = p_account_id;

  IF v_phone_lookup_hash IS NOT NULL THEN
    DELETE FROM public.otp_verifications WHERE phone_lookup_hash = v_phone_lookup_hash;
  END IF;
  UPDATE public.accounts
  SET phone_number = p_anonymized_phone, phone_lookup_hash = NULL,
      pin_hash = NULL, failed_pin_attempts = 0, locked_until = NULL,
      is_active = false, updated_at = now()
  WHERE id = p_account_id;
  INSERT INTO public.deletion_log (resource_type, resource_id, reason, metadata)
  VALUES (
    'patient_account',
    'sha256:' || encode(digest(p_account_id::text, 'sha256'), 'hex'),
    'Patient account data erased or anonymized',
    jsonb_build_object('profiles_anonymized', v_profile_count)
  );
  RETURN jsonb_build_object('success', true, 'profiles_anonymized', v_profile_count);
END;
$function$;

REVOKE ALL ON FUNCTION public.erase_patient_account_atomic(uuid, text) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.erase_patient_account_atomic(uuid, text) TO service_role;

CREATE OR REPLACE FUNCTION public.save_doctor_action_atomic(p_action jsonb, p_fingerprint text)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY INVOKER
 SET search_path TO 'public', 'pg_temp'
AS $function$
DECLARE
  v_consultation_id uuid;
  v_existing public.doctor_actions%ROWTYPE;
  v_inserted public.doctor_actions%ROWTYPE;
BEGIN
  v_consultation_id := nullif(p_action->>'consultation_id', '')::uuid;
  IF v_consultation_id IS NULL OR p_fingerprint IS NULL OR p_fingerprint = '' THEN
    RAISE EXCEPTION 'Consultation and idempotency fingerprint are required';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended(v_consultation_id::text, 0));
  SELECT * INTO v_existing FROM public.doctor_actions
  WHERE consultation_id = v_consultation_id
    AND idempotency_fingerprint = p_fingerprint
    AND created_at >= now() - interval '10 seconds'
  ORDER BY created_at DESC LIMIT 1 FOR UPDATE;
  IF FOUND THEN
    RETURN jsonb_build_object('duplicate', true, 'row', to_jsonb(v_existing));
  END IF;
  INSERT INTO public.doctor_actions (
    id, action_id, consultation_id, doctor_id, action_type, notes, details,
    idempotency_fingerprint, created_at
  ) VALUES (
    (p_action->>'id')::uuid, p_action->>'action_id', v_consultation_id,
    nullif(p_action->>'doctor_id', '')::uuid, p_action->>'action_type',
    p_action->>'notes', coalesce(p_action->'details', '{}'::jsonb), p_fingerprint,
    coalesce(nullif(p_action->>'created_at', '')::timestamptz, now())
  ) RETURNING * INTO v_inserted;
  RETURN jsonb_build_object('duplicate', false, 'row', to_jsonb(v_inserted));
END;
$function$;

REVOKE ALL ON FUNCTION public.save_doctor_action_atomic(jsonb, text) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.save_doctor_action_atomic(jsonb, text) TO service_role;

