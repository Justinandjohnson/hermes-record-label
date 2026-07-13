import { resolveApiAuth } from "./hermes-bridge";

export const AUDIO_EXTENSIONS = [".wav", ".flac", ".mp3", ".aiff", ".aif", ".ogg", ".m4a"];

export function isAudioFile(name: string): boolean {
  return AUDIO_EXTENSIONS.some((ext) => name.toLowerCase().endsWith(ext));
}

interface FSEntry { isDirectory: boolean; isFile: boolean; name: string }
interface FSFileEntry extends FSEntry { file(ok: (f: File) => void, err?: (e: Error) => void): void }
interface FSDirEntry extends FSEntry { createReader(): FSDirReader }
interface FSDirReader { readEntries(ok: (e: FSEntry[]) => void, err?: (e: Error) => void): void }

function fileFromEntry(e: FSFileEntry): Promise<File> {
  return new Promise((ok, err) => e.file(ok, err));
}

async function collectDir(dir: FSDirEntry): Promise<File[]> {
  return new Promise((resolve) => {
    const reader = dir.createReader();
    const all: File[] = [];
    function read() {
      reader.readEntries(async (entries) => {
        if (!entries.length) { resolve(all); return; }
        for (const entry of entries) {
          if (entry.isFile && isAudioFile(entry.name))
            all.push(await fileFromEntry(entry as FSFileEntry));
          else if (entry.isDirectory)
            all.push(...await collectDir(entry as FSDirEntry));
        }
        read();
      });
    }
    read();
  });
}

export async function collectAudioFromDrop(
  transfer: DataTransfer,
): Promise<{ files: File[]; name: string }> {
  // Snapshot synchronously — DataTransfer items are cleared after the event handler returns
  type Snapshot = { entry: FSEntry | null; file: File | null };
  const snapshots: Snapshot[] = [];
  for (let i = 0; i < transfer.items.length; i++) {
    const item = transfer.items[i];
    const entry = item.webkitGetAsEntry?.() as FSEntry | null;
    snapshots.push({ entry, file: entry ? null : item.getAsFile() });
  }

  let name = "Dropped Files";
  const files: File[] = [];

  for (let i = 0; i < snapshots.length; i++) {
    const { entry, file } = snapshots[i];

    if (!entry) {
      if (file && isAudioFile(file.name)) files.push(file);
      continue;
    }

    if (entry.isDirectory) {
      if (i === 0) name = entry.name;
      files.push(...await collectDir(entry as FSDirEntry));
    } else if (entry.isFile && isAudioFile(entry.name)) {
      files.push(await fileFromEntry(entry as FSFileEntry));
    }
  }

  return { files, name };
}

export interface IntakeResult {
  album: string;
  tracks_added: number;
  project_id: number | string;
}

export async function uploadFiles(
  files: File[],
  albumName: string,
  onProgress: (pct: number) => void,
): Promise<IntakeResult> {
  const { url: baseUrl, token } = await resolveApiAuth();
  return new Promise((resolve, reject) => {
    const form = new FormData();
    form.append("album_name", albumName);
    for (const f of files) form.append("files", f, f.name);

    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${baseUrl}/api/intake`);
    if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);

    xhr.upload.addEventListener("progress", (ev) => {
      if (ev.lengthComputable) onProgress(Math.round((ev.loaded / ev.total) * 100));
    });

    xhr.addEventListener("load", () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try { resolve(JSON.parse(xhr.responseText) as IntakeResult); }
        catch { reject(new Error("Invalid JSON from server")); }
      } else {
        reject(new Error(xhr.responseText || `HTTP ${xhr.status}`));
      }
    });

    xhr.addEventListener("error", () => reject(new Error("Network error")));
    xhr.addEventListener("abort", () => reject(new Error("Aborted")));
    xhr.send(form);
  });
}
