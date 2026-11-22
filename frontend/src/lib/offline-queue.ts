/**
 * Offline Consultation Queue & Low-Bandwidth Sync for Grannus RuralCare AI.
 * 
 * Supports ASHA workers and village sub-centres operating in zero-connectivity
 * zones by queuing intake payloads locally and automatically synchronizing
 * in memory for the current page session and synchronizing when requested.
 */

import { getAuthHeaders } from './api';
import { audioDataToBlob } from './utils';

export interface OfflineConsultation {
  id: string;
  createdAt: number;
  payload: {
    audio: string; // base64
    language_code: string;
    doctor_preferred_language: string;
    age?: string;
    gender?: string;
    reported_duration?: string;
    known_conditions?: string;
    current_medications?: string;
    consent_id: string;
  };
  retryCount: number;
  lastError?: string;
}

// Raw patient audio is never written to localStorage, IndexedDB, or sessionStorage.
let offlineQueue: OfflineConsultation[] = [];

export function getOfflineQueue(): OfflineConsultation[] {
  return [...offlineQueue];
}

export function saveOfflineConsultation(payload: OfflineConsultation['payload']): string {
  const id = `offline_${Date.now()}_${Math.random().toString(36).substring(2, 7)}`;
  const item: OfflineConsultation = {
    id,
    createdAt: Date.now(),
    payload,
    retryCount: 0,
  };
  offlineQueue.push(item);
  return id;
}

export function removeOfflineConsultation(id: string) {
  offlineQueue = offlineQueue.filter((item) => item.id !== id);
}

export async function syncOfflineConsultations(
  apiUrl: string,
  onProgress?: (synced: number, total: number) => void
): Promise<{ success: number; failed: number }> {
  const queue = getOfflineQueue();
  if (queue.length === 0) return { success: 0, failed: 0 };

  let successCount = 0;
  let failedCount = 0;

  for (let i = 0; i < queue.length; i++) {
    const item = queue[i];
    try {
      // Convert base64 back to Blob for multipart upload safely without network fetch
      const audioBlob = await audioDataToBlob(item.payload.audio);

      const formData = new FormData();
      formData.append('audio', audioBlob, 'patient_recording.wav');
      formData.append('consent_id', item.payload.consent_id);
      formData.append('language_code', item.payload.language_code || 'unknown');
      formData.append('doctor_preferred_language', item.payload.doctor_preferred_language || 'en-IN');
      if (item.payload.age) formData.append('age', item.payload.age);
      if (item.payload.gender) formData.append('gender', item.payload.gender);
      if (item.payload.reported_duration) formData.append('reported_duration', item.payload.reported_duration);
      if (item.payload.known_conditions) formData.append('known_conditions', item.payload.known_conditions);
      if (item.payload.current_medications) formData.append('current_medications', item.payload.current_medications);

      const response = await fetch(`${apiUrl}/api/v1/pipeline/process-audio`, {
        method: 'POST',
        headers: getAuthHeaders(),
        credentials: 'include',
        body: formData,
      });

      if (!response.ok) {
        throw new Error(`Upload failed with status ${response.status}`);
      }

      removeOfflineConsultation(item.id);
      successCount++;
    } catch (err: any) {
      console.warn(`Sync failed for offline item ${item.id} (${err instanceof Error ? err.name : 'unknown error'})`);
      item.retryCount += 1;
      item.lastError = err.message || 'Network error';
      failedCount++;
    }

    if (onProgress) {
      onProgress(i + 1, queue.length);
    }
  }

  return { success: successCount, failed: failedCount };
}
