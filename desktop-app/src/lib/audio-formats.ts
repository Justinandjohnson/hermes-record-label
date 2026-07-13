export const SUPPORTED_EXTENSIONS = [
  ".wav",
  ".flac",
  ".mp3",
  ".aiff",
  ".aif",
  ".ogg",
] as const;

export const SUPPORTED_MIMES = [
  "audio/wav",
  "audio/x-wav",
  "audio/flac",
  "audio/mpeg",
  "audio/aiff",
  "audio/x-aiff",
  "audio/ogg",
] as const;

export const MIN_FILE_SIZE = 10 * 1024; // 10KB
export const MAX_FILE_SIZE = 200 * 1024 * 1024; // 200MB

export function isSupported(filename: string): boolean {
  const ext = filename.slice(filename.lastIndexOf(".")).toLowerCase();
  return (SUPPORTED_EXTENSIONS as readonly string[]).includes(ext);
}

export function validateFile(file: File): { valid: boolean; reason?: string } {
  if (!isSupported(file.name)) {
    return { valid: false, reason: `Unsupported format. Use: ${SUPPORTED_EXTENSIONS.join(", ")}` };
  }
  if (file.size < MIN_FILE_SIZE) {
    return { valid: false, reason: "File too small (< 10KB)" };
  }
  if (file.size > MAX_FILE_SIZE) {
    return { valid: false, reason: "File too large (> 200MB)" };
  }
  return { valid: true };
}

export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
