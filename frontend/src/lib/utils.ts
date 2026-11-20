import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function base64ToBlob(base64Data: string, defaultMime = 'audio/webm'): Blob {
  if (!base64Data || typeof base64Data !== 'string') {
    return new Blob([], { type: defaultMime });
  }

  try {
    let mime = defaultMime;
    let rawBase64 = base64Data.trim();

    if (rawBase64.startsWith('data:')) {
      const commaIndex = rawBase64.indexOf(',');
      if (commaIndex !== -1) {
        const meta = rawBase64.substring(5, commaIndex);
        const mimeMatch = meta.match(/^([^;]+)/);
        if (mimeMatch) {
          mime = mimeMatch[1];
        }
        rawBase64 = rawBase64.substring(commaIndex + 1);
      }
    }

    // Clean whitespace/newlines
    rawBase64 = rawBase64.replace(/\s/g, '');

    // Add padding if missing
    while (rawBase64.length % 4 !== 0) {
      rawBase64 += '=';
    }

    const bstr = atob(rawBase64);
    let n = bstr.length;
    const u8arr = new Uint8Array(n);
    while (n--) {
      u8arr[n] = bstr.charCodeAt(n);
    }
    return new Blob([u8arr], { type: mime });
  } catch (err) {
    console.warn("base64ToBlob conversion failed:", err);
    return new Blob([], { type: defaultMime });
  }
}

export async function audioDataToBlob(audioData: unknown, defaultMime = 'audio/webm'): Promise<Blob> {
  if (!audioData) {
    return new Blob([], { type: defaultMime });
  }

  // Already a Blob or File
  if (audioData instanceof Blob) {
    return audioData;
  }

  if (typeof audioData === 'string') {
    const trimmed = audioData.trim();
    if (!trimmed) {
      return new Blob([], { type: defaultMime });
    }

    // Direct base64 or data: URI
    if (trimmed.startsWith('data:') || (!trimmed.startsWith('http://') && !trimmed.startsWith('https://') && !trimmed.startsWith('blob:'))) {
      return base64ToBlob(trimmed, defaultMime);
    }

    // HTTP / blob URL fallback with protected fetch
    try {
      const res = await fetch(trimmed);
      return await res.blob();
    } catch (fetchErr) {
      console.warn("fetch audio URL failed:", fetchErr);
      return new Blob([], { type: defaultMime });
    }
  }

  return new Blob([], { type: defaultMime });
}
