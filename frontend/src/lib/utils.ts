import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function base64ToBlob(base64Data: string, defaultMime = 'audio/webm'): Blob {
  if (base64Data.startsWith('data:')) {
    const parts = base64Data.split(',');
    const mimeMatch = parts[0].match(/:(.*?);/);
    const mime = mimeMatch ? mimeMatch[1] : defaultMime;
    const bstr = atob(parts[1]);
    let n = bstr.length;
    const u8arr = new Uint8Array(n);
    while (n--) {
      u8arr[n] = bstr.charCodeAt(n);
    }
    return new Blob([u8arr], { type: mime });
  }

  try {
    const bstr = atob(base64Data);
    let n = bstr.length;
    const u8arr = new Uint8Array(n);
    while (n--) {
      u8arr[n] = bstr.charCodeAt(n);
    }
    return new Blob([u8arr], { type: defaultMime });
  } catch {
    return new Blob([], { type: defaultMime });
  }
}

export async function audioDataToBlob(audioData: string, defaultMime = 'audio/webm'): Promise<Blob> {
  if (typeof audioData === 'string' && (audioData.startsWith('data:') || (!audioData.startsWith('http://') && !audioData.startsWith('https://') && !audioData.startsWith('blob:')))) {
    try {
      return base64ToBlob(audioData, defaultMime);
    } catch {
      // Fall through to fetch
    }
  }
  const res = await fetch(audioData);
  return await res.blob();
}
