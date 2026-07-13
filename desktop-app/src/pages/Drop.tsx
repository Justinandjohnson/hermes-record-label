import { useState, useCallback, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { handleFileDrop as tauriHandleFileDrop, isTauri, resolveApiAuth } from "../lib/hermes-bridge";
import { formatFileSize } from "../lib/audio-formats";
import { collectAudioFromDrop, uploadFiles, AUDIO_EXTENSIONS, isAudioFile } from "../lib/intake";

type PageState = "idle" | "collecting" | "uploading" | "error";
type FileStatus = "uploading" | "done" | "error";

interface FileEntry {
  id: string;
  file: File;
  status: FileStatus;
}

function uid(): string {
  return Math.random().toString(36).slice(2, 9);
}

function WaveformBars() {
  return (
    <div className="flex items-end gap-1 h-12">
      {[0, 1, 2, 3, 4].map((i) => (
        <div
          key={i}
          className="w-2 bg-label-500 rounded-sm"
          style={{
            animation: "waveBar 0.8s ease-in-out infinite alternate",
            animationDelay: `${i * 0.12}s`,
          }}
        />
      ))}
      <style>{`
        @keyframes waveBar {
          from { height: 8px; opacity: 0.4; }
          to   { height: 40px; opacity: 1; }
        }
      `}</style>
    </div>
  );
}

function StatusBadge({ status }: { status: FileStatus }) {
  const map: Record<FileStatus, { label: string; cls: string }> = {
    uploading: { label: "uploading", cls: "bg-blue-900/60 text-blue-300 animate-pulse" },
    done:      { label: "done",      cls: "bg-emerald-900/60 text-emerald-300" },
    error:     { label: "error",     cls: "bg-red-900/60 text-red-300" },
  };
  const { label, cls } = map[status];
  return (
    <span className={`text-[10px] font-mono px-2 py-0.5 rounded-full ${cls}`}>
      {label}
    </span>
  );
}

function FileList({ entries }: { entries: FileEntry[] }) {
  return (
    <ul className="w-full space-y-1.5 max-h-64 overflow-y-auto pr-1">
      {entries.map((e) => (
        <li
          key={e.id}
          className="flex items-center gap-3 bg-surface-2 rounded-lg px-3 py-2 text-sm"
        >
          <span className="text-base select-none">🎵</span>
          <span className="flex-1 truncate text-zinc-300 font-mono text-xs">{e.file.name}</span>
          <span className="text-zinc-500 text-xs shrink-0">{formatFileSize(e.file.size)}</span>
          <StatusBadge status={e.status} />
        </li>
      ))}
    </ul>
  );
}

export default function Drop() {
  const navigate = useNavigate();
  const [state, setState] = useState<PageState>("idle");
  const [dragOver, setDragOver] = useState(false);
  const [albumName, setAlbumName] = useState("");
  const [entries, setEntries] = useState<FileEntry[]>([]);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [pathInput, setPathInput] = useState("");

  const folderInputRef = useRef<HTMLInputElement>(null);

  const reset = useCallback(() => {
    setState("idle");
    setDragOver(false);
    setAlbumName("");
    setEntries([]);
    setUploadProgress(0);
    setErrorMsg(null);
    setPathInput("");
    if (folderInputRef.current) folderInputRef.current.value = "";
  }, []);

  const runUpload = useCallback(async (files: File[], name: string) => {
    setAlbumName(name);
    setEntries(files.map((f) => ({ id: uid(), file: f, status: "uploading" as FileStatus })));
    setState("uploading");
    setUploadProgress(0);
    setErrorMsg(null);

    try {
      await uploadFiles(files, name, (pct) => setUploadProgress(pct));
      setEntries((prev) => prev.map((e) => ({ ...e, status: "done" as FileStatus })));

      if (isTauri()) {
        for (const f of files) {
          try { await tauriHandleFileDrop(f.name); } catch { /* non-fatal */ }
        }
      }

      navigate("/");
    } catch (err) {
      setEntries((prev) => prev.map((e) => ({ ...e, status: "error" as FileStatus })));
      setErrorMsg(String(err));
      setState("error");
    }
  }, [navigate]);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(true);
  }, []);

  const handleDragLeave = useCallback(() => {
    setDragOver(false);
  }, []);

  const handleDrop = useCallback(async (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    setState("collecting");

    const { files, name } = await collectAudioFromDrop(e.dataTransfer);

    if (files.length === 0) {
      setErrorMsg("No supported audio files found.");
      setState("error");
      return;
    }

    await runUpload(files, name);
  }, [runUpload]);

  const handleFolderInput = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files || files.length === 0) return;

    const audioFiles: File[] = [];
    let folderName = "Album";

    for (let i = 0; i < files.length; i++) {
      const f = files[i];
      if (i === 0) folderName = f.webkitRelativePath.split("/")[0] || f.name;
      if (isAudioFile(f.name)) audioFiles.push(f);
    }

    if (audioFiles.length === 0) {
      setErrorMsg("No supported audio files found in that folder.");
      setState("error");
      return;
    }

    await runUpload(audioFiles, folderName);
  }, [runUpload]);

  const handlePathSubmit = useCallback(async (e: React.SyntheticEvent<HTMLFormElement>) => {
    e.preventDefault();
    const path = pathInput.trim();
    if (!path) return;

    setState("uploading");
    setErrorMsg(null);

    try {
      const { url: baseUrl, token } = await resolveApiAuth();
      const headers: Record<string, string> = { "Content-Type": "application/json" };
      if (token) headers.Authorization = `Bearer ${token}`;

      const res = await fetch(`${baseUrl}/api/intake`, {
        method: "POST",
        headers,
        body: JSON.stringify({ folder_path: path }),
      });

      if (!res.ok) {
        const text = await res.text().catch(() => `HTTP ${res.status}`);
        throw new Error(text || `HTTP ${res.status}`);
      }

      navigate("/");
    } catch (err) {
      setErrorMsg(String(err));
      setState("error");
    }
  }, [pathInput, navigate]);

  return (
    <div className="p-6 h-full flex flex-col gap-6">
      <h1 className="text-2xl font-bold text-zinc-100 shrink-0">Drop</h1>

      {state === "idle" && (
        <div className="flex-1 flex flex-col gap-4">
          <div
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={(e) => void handleDrop(e)}
            className={`
              flex-1 flex flex-col items-center justify-center
              rounded-2xl border-2 border-dashed transition-all
              ${dragOver
                ? "border-label-500 bg-label-500/5 scale-[1.01]"
                : "border-surface-3 bg-surface-1 hover:border-zinc-500"
              }
            `}
          >
            <div className="text-center px-8 pointer-events-none">
              <div className={`text-6xl mb-6 transition-transform select-none ${dragOver ? "scale-125" : ""}`}>
                📁
              </div>
              <h2 className="text-xl font-semibold text-zinc-200 mb-2">
                {dragOver ? "Drop the folder" : "Drop an album folder here"}
              </h2>
              <p className="text-sm text-zinc-500 mb-5">
                All audio files inside will be collected automatically
              </p>
              <div className="flex items-center justify-center gap-2 flex-wrap mb-4">
                {AUDIO_EXTENSIONS.map((ext) => (
                  <span key={ext} className="text-[10px] font-mono bg-surface-2 text-zinc-500 px-2 py-1 rounded-md">
                    {ext}
                  </span>
                ))}
              </div>
            </div>
            <button
              type="button"
              onClick={() => folderInputRef.current?.click()}
              className="pointer-events-auto btn-ghost text-sm"
            >
              Browse Folder
            </button>
            <input
              ref={folderInputRef}
              type="file"
              // @ts-expect-error — webkitdirectory is non-standard but widely supported
              webkitdirectory=""
              multiple
              className="sr-only"
              onChange={(e) => void handleFolderInput(e)}
            />
          </div>

          <div className="bg-surface-1 rounded-xl border border-surface-3 p-4">
            <p className="text-xs text-zinc-500 mb-3">Or enter a folder path (Mac only)</p>
            <form onSubmit={(e) => void handlePathSubmit(e)} className="flex gap-2">
              <input
                type="text"
                value={pathInput}
                onChange={(e) => setPathInput(e.target.value)}
                placeholder="/Users/you/Music/AlbumName"
                className="flex-1 bg-surface-2 border border-surface-3 rounded-lg px-3 py-2 text-sm text-zinc-300 placeholder-zinc-600 focus:outline-none focus:border-zinc-500"
              />
              <button
                type="submit"
                disabled={!pathInput.trim()}
                className="px-4 py-2 bg-zinc-700 hover:bg-zinc-600 disabled:opacity-40 text-sm text-zinc-200 rounded-lg transition-colors"
              >
                Submit
              </button>
            </form>
          </div>
        </div>
      )}

      {state === "collecting" && (
        <div className="flex-1 flex flex-col items-center justify-center gap-4">
          <WaveformBars />
          <p className="text-sm text-zinc-400">Reading folder contents…</p>
        </div>
      )}

      {state === "uploading" && (
        <div className="flex-1 flex flex-col gap-5">
          <div className="flex items-baseline gap-3">
            <span className="text-base font-semibold text-zinc-300 truncate">{albumName}</span>
            <span className="text-xs text-zinc-500">uploading {entries.length} files…</span>
          </div>
          <div className="w-full bg-surface-2 rounded-full h-2 overflow-hidden">
            <div
              className="h-2 bg-label-500 rounded-full transition-all duration-300"
              style={{ width: `${uploadProgress}%` }}
            />
          </div>
          <p className="text-xs text-zinc-500 text-right -mt-3">{uploadProgress}%</p>
          <FileList entries={entries} />
        </div>
      )}

      {state === "error" && (
        <div className="flex-1 flex flex-col items-center justify-center gap-4">
          <div className="w-16 h-16 bg-red-600/20 border border-red-600/40 rounded-full flex items-center justify-center text-3xl">
            ✕
          </div>
          <div className="text-center">
            <h2 className="text-xl font-semibold text-zinc-200 mb-2">Something went wrong</h2>
            <p className="text-xs text-zinc-500 font-mono max-w-sm break-all">{errorMsg}</p>
          </div>
          <button onClick={reset} className="btn-ghost text-sm mt-2">
            Try again
          </button>
        </div>
      )}
    </div>
  );
}
