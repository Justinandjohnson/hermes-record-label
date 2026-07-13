import { useState, useEffect, type ReactNode } from "react";
import { useSettings } from "../hooks/useHermesDB";
import { getDataDir, getDbPath, isTauri } from "../lib/hermes-bridge";

const DAYS = [
  { key: "monday", label: "Mon" },
  { key: "tuesday", label: "Tue" },
  { key: "wednesday", label: "Wed" },
  { key: "thursday", label: "Thu" },
  { key: "friday", label: "Fri" },
  { key: "saturday", label: "Sat" },
  { key: "sunday", label: "Sun" },
];

function SectionHeading({ children }: { children: ReactNode }) {
  return (
    <h2 className="text-xs font-semibold text-zinc-500 uppercase tracking-widest mb-3">
      {children}
    </h2>
  );
}

function InputField({
  label,
  value,
  onChange,
  placeholder,
  helper,
  type = "text",
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  helper?: string;
  type?: string;
}) {
  return (
    <div>
      <label className="block text-sm font-medium text-zinc-300 mb-1">{label}</label>
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full bg-surface-2 border border-surface-3 rounded-lg px-3 py-2 text-sm text-zinc-200 placeholder-zinc-600 focus:outline-none focus:border-label-500 transition-colors"
      />
      {helper && <p className="text-[11px] text-zinc-600 mt-1">{helper}</p>}
    </div>
  );
}

function Toggle({ enabled, onChange }: { enabled: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
      onClick={() => onChange(!enabled)}
      className={`w-12 h-7 rounded-full transition-colors relative shrink-0 ${
        enabled ? "bg-label-500" : "bg-surface-3"
      }`}
      role="switch"
      aria-checked={enabled}
    >
      <div
        className={`w-5 h-5 bg-white rounded-full absolute top-1 transition-transform shadow-sm ${
          enabled ? "translate-x-6" : "translate-x-1"
        }`}
      />
    </button>
  );
}

export default function Settings() {
  const { settings, saveSettings, loading } = useSettings();

  // Local draft state — synced from settings once loaded
  const [abletonProjectFolder, setAbletonProjectFolder] = useState("");
  const [abletonExportFolder, setAbletonExportFolder] = useState("");
  const [artistName, setArtistName] = useState("");
  const [artistPhone, setArtistPhone] = useState("");
  const [dnd, setDnd] = useState(false);
  const [quietStart, setQuietStart] = useState("22:00");
  const [quietEnd, setQuietEnd] = useState("09:00");
  const [quietDays, setQuietDays] = useState<string[]>([]);
  const [remoteUrl, setRemoteUrl] = useState("");
  const [apiToken, setApiToken] = useState("");

  const [dataDir, setDataDir] = useState<string | null>(null);
  const [dbPath, setDbPath] = useState<string | null>(null);
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved" | "error">("idle");

  // Sync from loaded settings
  useEffect(() => {
    if (!loading) {
      setAbletonProjectFolder(settings.ableton_project_folder);
      setAbletonExportFolder(settings.ableton_export_folder);
      setArtistName(settings.artist_name);
      setArtistPhone(settings.artist_phone);
      setDnd(settings.dnd_enabled);
      setQuietStart(settings.quiet_hours_start);
      setQuietEnd(settings.quiet_hours_end);
      setQuietDays(settings.quiet_days);
      setRemoteUrl(settings.remote_url ?? "");
      setApiToken(settings.api_token ?? "");
    }
  }, [loading, settings]);

  // Load diagnostics paths
  useEffect(() => {
    getDataDir()
      .then(setDataDir)
      .catch(() => setDataDir("unavailable"));
    getDbPath()
      .then(setDbPath)
      .catch(() => setDbPath("unavailable"));
  }, []);

  const toggleDay = (day: string) =>
    setQuietDays((prev) =>
      prev.includes(day) ? prev.filter((d) => d !== day) : [...prev, day],
    );

  const handleSave = async () => {
    setSaveState("saving");
    try {
      await saveSettings({
        ableton_project_folder: abletonProjectFolder,
        ableton_export_folder: abletonExportFolder,
        artist_name: artistName,
        artist_phone: artistPhone,
        quiet_hours_start: quietStart,
        quiet_hours_end: quietEnd,
        quiet_days: quietDays,
        dnd_enabled: dnd,
        remote_url: remoteUrl,
        api_token: apiToken,
      });
      setSaveState("saved");
      setTimeout(() => setSaveState("idle"), 2500);
    } catch (err) {
      console.error("Failed to save settings:", err);
      setSaveState("error");
      setTimeout(() => setSaveState("idle"), 3000);
    }
  };

  if (loading) {
    return (
      <div className="p-8 max-w-xl">
        <div className="animate-pulse space-y-4">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="h-12 bg-surface-1 rounded-xl" />
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="p-6 max-w-xl pb-24">
      <h1 className="text-2xl font-bold text-zinc-100 mb-8">Settings</h1>

      <div className="space-y-8">
        {/* ── Music Intake ──────────────────────────────────── */}
        <section>
          <SectionHeading>Music Intake</SectionHeading>
          <div className="card space-y-5">

            {/* Flow diagram */}
            <div className="flex items-center gap-2 text-xs text-zinc-500 flex-wrap">
              <span className="bg-surface-2 border border-surface-3 rounded-lg px-3 py-1.5 font-mono text-zinc-300">Ableton folder</span>
              <span>→</span>
              <span className="bg-blue-950/40 border border-blue-800/40 rounded-lg px-3 py-1.5 text-blue-300">Google Drive</span>
              <span>→</span>
              <span className="bg-orange-950/40 border border-orange-800/40 rounded-lg px-3 py-1.5 text-orange-300">Backblaze B2</span>
            </div>
            <p className="text-[11px] text-zinc-600 -mt-2">
              Set your Ableton bounce folder below. Every new audio file that lands there gets registered, pushed to Google Drive, and synced to the B2 vault automatically.
            </p>

            {/* Ableton export folder — the watched folder */}
            <div>
              <label className="block text-sm font-medium text-zinc-300 mb-1">
                Ableton Export / Bounce Folder
              </label>
              <input
                type="text"
                value={abletonExportFolder}
                onChange={(e) => setAbletonExportFolder(e.target.value)}
                placeholder="/Users/you/Music/Ableton/Exports  or  ~/Desktop/Bounces"
                className="w-full bg-surface-2 border border-surface-3 rounded-lg px-3 py-2 text-sm text-zinc-200 placeholder-zinc-600 focus:outline-none focus:border-label-500 transition-colors font-mono text-xs"
              />
              <p className="text-[11px] text-zinc-600 mt-1">
                Paste the folder path where Ableton saves your exported/bounced tracks. The watcher monitors this folder recursively.
              </p>
            </div>

            {/* Sync destinations — informational */}
            <div className="border-t border-surface-2 pt-4 space-y-2">
              <p className="text-xs text-zinc-500 font-medium">Sync destinations (automatic)</p>
              <div className="flex items-center gap-2 text-[11px] text-zinc-500">
                <span className="text-blue-400">🗂</span>
                <span className="font-mono text-zinc-400">Google Drive → AI Record Label/inbox</span>
              </div>
              <div className="flex items-center gap-2 text-[11px] text-zinc-500">
                <span className="text-orange-400">☁</span>
                <span className="font-mono text-zinc-400">Backblaze B2 → ai-record-label-vault</span>
              </div>
            </div>

            {/* Ableton project folder — optional, for session context */}
            <div className="border-t border-surface-2 pt-4">
              <label className="block text-sm font-medium text-zinc-300 mb-1">
                Ableton Project Folder <span className="text-zinc-600 font-normal text-xs">(optional — for session tracking)</span>
              </label>
              <input
                type="text"
                value={abletonProjectFolder}
                onChange={(e) => setAbletonProjectFolder(e.target.value)}
                placeholder="/Users/you/Music/Ableton/Projects"
                className="w-full bg-surface-2 border border-surface-3 rounded-lg px-3 py-2 text-sm text-zinc-200 placeholder-zinc-600 focus:outline-none focus:border-label-500 transition-colors font-mono text-xs"
              />
            </div>
          </div>
        </section>

        {/* ── Artist Profile ────────────────────────────────── */}
        <section>
          <SectionHeading>Artist Profile</SectionHeading>
          <div className="card space-y-4">
            <InputField
              label="Artist Name"
              value={artistName}
              onChange={setArtistName}
              placeholder="Your artist name"
            />
            <InputField
              label="Phone Number"
              value={artistPhone}
              onChange={setArtistPhone}
              placeholder="+1 555 000 0000"
              type="tel"
              helper="Used by the team to send you SMS feedback and alerts"
            />
          </div>
        </section>

        {/* ── Do Not Disturb ────────────────────────────────── */}
        <section>
          <SectionHeading>Do Not Disturb</SectionHeading>
          <div className="card space-y-5">
            {/* DND toggle */}
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm font-medium text-zinc-200">DND Mode</p>
                <p className="text-xs text-zinc-500">Pause all agent messages immediately</p>
              </div>
              <Toggle enabled={dnd} onChange={setDnd} />
            </div>

            <div className="border-t border-surface-2" />

            {/* Quiet hours */}
            <div>
              <label className="block text-xs text-zinc-500 mb-2">Quiet hours</label>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-[10px] text-zinc-600 mb-1">Start</label>
                  <input
                    type="time"
                    value={quietStart}
                    onChange={(e) => setQuietStart(e.target.value)}
                    className="w-full bg-surface-2 border border-surface-3 rounded-lg px-3 py-2 text-sm text-zinc-200 focus:outline-none focus:border-label-500 transition-colors"
                  />
                </div>
                <div>
                  <label className="block text-[10px] text-zinc-600 mb-1">End</label>
                  <input
                    type="time"
                    value={quietEnd}
                    onChange={(e) => setQuietEnd(e.target.value)}
                    className="w-full bg-surface-2 border border-surface-3 rounded-lg px-3 py-2 text-sm text-zinc-200 focus:outline-none focus:border-label-500 transition-colors"
                  />
                </div>
              </div>
            </div>

            {/* Day chips */}
            <div>
              <label className="block text-xs text-zinc-500 mb-2">DND days</label>
              <div className="flex gap-2 flex-wrap">
                {DAYS.map(({ key, label }) => (
                  <button
                    key={key}
                    onClick={() => toggleDay(key)}
                    className={`text-xs px-3 py-1.5 rounded-lg transition-colors font-medium ${
                      quietDays.includes(key)
                        ? "bg-label-500 text-black"
                        : "bg-surface-2 text-zinc-400 hover:text-zinc-200 hover:bg-surface-3"
                    }`}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>
          </div>
        </section>

        {/* ── Remote Server (Windows / multi-device mode) ──── */}
        {/* Hidden in browser mode — the web app IS the remote connection */}
        {isTauri() && (
        <section>
          <SectionHeading>Remote Server</SectionHeading>
          <div className="card space-y-4">
            <p className="text-[11px] text-zinc-500">
              On Windows — point this at your Mac's tunnel URL so the app reads data from the Mac instead of a local database. Leave blank on the Mac itself.
            </p>
            <InputField
              label="Tunnel URL"
              value={remoteUrl}
              onChange={setRemoteUrl}
              placeholder="https://xxxx.trycloudflare.com"
              helper="Found in launch.sh output or ~/Library/Application Support/ai-record-label/.cloudflared.url on Mac"
            />
            <InputField
              label="API Token"
              value={apiToken}
              onChange={setApiToken}
              placeholder="Paste token from Mac"
              type="password"
              helper="Run: cat ~/Library/'Application Support'/ai-record-label/api_token.txt on the Mac"
            />
            {remoteUrl && (
              <div className="flex items-center gap-2">
                <div className={`w-2 h-2 rounded-full ${remoteUrl.startsWith("https://") ? "bg-emerald-500" : "bg-red-500"}`} />
                <span className="text-[11px] text-zinc-500">
                  {remoteUrl.startsWith("https://") ? "Remote mode active — fetching from Mac" : "URL must start with https://"}
                </span>
              </div>
            )}
          </div>
        </section>
        )}

        {/* ── About / Diagnostics ───────────────────────────── */}
        <section>
          <SectionHeading>About &amp; Diagnostics</SectionHeading>
          <div className="card space-y-3">
            <DiagRow label="App Version" value="0.1.0" />
            <DiagRow label="Data Directory" value={dataDir ?? "loading…"} mono />
            <DiagRow label="Database" value={dbPath ?? "loading…"} mono />
          </div>
        </section>
      </div>

      {/* Sticky save bar */}
      <div className="fixed bottom-0 left-16 sm:left-32 right-0 bg-surface-0/90 backdrop-blur border-t border-surface-2 px-6 py-4 flex items-center justify-between">
        <span className={`text-sm transition-colors ${
          saveState === "saved" ? "text-emerald-400" :
          saveState === "error" ? "text-red-400" :
          "text-zinc-600"
        }`}>
          {saveState === "saved" && "✓ Saved"}
          {saveState === "error" && "Failed to save"}
        </span>
        <button
          onClick={handleSave}
          disabled={saveState === "saving"}
          className="btn-primary text-sm disabled:opacity-50"
        >
          {saveState === "saving" ? "Saving…" : "Save settings"}
        </button>
      </div>
    </div>
  );
}

function DiagRow({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex items-start justify-between gap-4">
      <span className="text-xs text-zinc-500 shrink-0">{label}</span>
      <span className={`text-xs text-zinc-400 text-right break-all ${mono ? "font-mono" : ""}`}>
        {value}
      </span>
    </div>
  );
}
