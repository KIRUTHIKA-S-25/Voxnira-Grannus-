export interface PipelinePayload {
  audio: Blob | string;
  language_code?: string;
  doctor_preferred_language?: string;
  age?: string;
  gender?: string;
  reported_duration?: string;
  known_conditions?: string;
  current_medications?: string;
  profile_id?: string;
  patient_name?: string;
  latitude?: number;
  longitude?: number;
  consent_id?: string;
}

// Keep uploaded patient audio in memory only; never copy it to browser storage.
let activePayload: PipelinePayload | null = null;

// Delete raw audio left in browser storage by pre-hardening app versions.
if (typeof window !== 'undefined') {
  try {
    sessionStorage.removeItem('grannus_pipeline_payload');
    localStorage.removeItem('grannus_offline_queue_v1');
  } catch {
    // Storage may be disabled by the browser.
  }
}

export function setPipelinePayload(payload: PipelinePayload) {
  activePayload = payload;
}

export function getPipelinePayload(): PipelinePayload | null {
  return activePayload;
}

export function clearPipelinePayload() {
  activePayload = null;
}
