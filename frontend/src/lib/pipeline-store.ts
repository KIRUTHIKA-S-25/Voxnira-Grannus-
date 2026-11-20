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
  dpdp_consent_verified?: boolean;
}

// In-memory payload cache for direct SPA page navigation without serialization loss
let activePayload: PipelinePayload | null = null;

export function setPipelinePayload(payload: PipelinePayload) {
  activePayload = payload;

  if (typeof window !== 'undefined') {
    try {
      if (typeof payload.audio === 'string') {
        sessionStorage.setItem('grannus_pipeline_payload', JSON.stringify(payload));
      } else if (payload.audio instanceof Blob) {
        // Attempt base64 backup in sessionStorage if under 4MB so browser refresh works
        if (payload.audio.size < 4 * 1024 * 1024) {
          const reader = new FileReader();
          reader.onloadend = () => {
            try {
              sessionStorage.setItem(
                'grannus_pipeline_payload',
                JSON.stringify({ ...payload, audio: reader.result as string })
              );
            } catch {
              // Ignore sessionStorage quota limits in private browsing
            }
          };
          reader.readAsDataURL(payload.audio);
        } else {
          sessionStorage.setItem(
            'grannus_pipeline_payload',
            JSON.stringify({ ...payload, audio: '' })
          );
        }
      }
    } catch {
      // Storage unavailable or disabled
    }
  }
}

export function getPipelinePayload(): PipelinePayload | null {
  if (activePayload) {
    return activePayload;
  }

  if (typeof window !== 'undefined') {
    try {
      const raw = sessionStorage.getItem('grannus_pipeline_payload');
      if (raw) {
        const parsed = JSON.parse(raw);
        activePayload = parsed;
        return parsed;
      }
    } catch {
      return null;
    }
  }

  return null;
}

export function clearPipelinePayload() {
  activePayload = null;
  if (typeof window !== 'undefined') {
    try {
      sessionStorage.removeItem('grannus_pipeline_payload');
    } catch {
      // Ignore
    }
  }
}
