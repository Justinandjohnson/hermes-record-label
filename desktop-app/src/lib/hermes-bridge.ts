// ── Tauri detection ──────────────────────────────────────────────────────────
// Tauri injects __TAURI_INTERNALS__ on the window object.
// When absent, we're running in a plain browser served by the Python API.

import type { Verdict } from "./verdict";

export function isTauri(): boolean {
  return typeof window !== "undefined" && !!(window as any).__TAURI_INTERNALS__;
}

// Lazy-load Tauri invoke so the import doesn't crash in browsers
let _invoke: ((cmd: string, args?: Record<string, unknown>) => Promise<any>) | null = null;

async function tauriInvoke<T>(cmd: string, args?: Record<string, unknown>): Promise<T> {
  if (!_invoke) {
    const mod = await import("@tauri-apps/api/core");
    _invoke = mod.invoke;
  }
  return _invoke!(cmd, args) as Promise<T>;
}

export interface Track {
  id: number;
  title: string | null;
  file_path: string;
  file_hash: string;
  file_size: number | null;
  duration_seconds: number | null;
  format: string | null;
  parent_track_id: number | null;
  version: number;
  state: string;
  project_id: number | null;
  created_at: string;
  updated_at: string;
}

export interface Feedback {
  id: number;
  track_id: number | null;
  project_id: number | null;
  agent: string;
  message: string;
  channel: string;
  direction: string;
  intent: string | null;
  created_at: string;
}

export interface Project {
  id: number;
  title: string;
  type: string;
  state: string;
  target_track_count: number | null;
  target_release_date: string | null;
  created_at: string;
}

export interface ArtistProfile {
  id: number;
  name: string;
  genre: string | null;
  subgenres: string | null;
  influences: string | null;
  sound_description: string | null;
  bandcamp_url: string | null;
  quiet_hours_start: string | null;
  quiet_hours_end: string | null;
  quiet_days: string | null;
  timezone: string;
  onboarded_at: string | null;
}

export interface ReleaseStateEntry {
  id: number;
  track_id: number;
  from_state: string | null;
  to_state: string;
  changed_by: string;
  reason: string | null;
  bandcamp_job_id: string | null;
  created_at: string;
}

export interface Stats {
  current_streak: number;
  longest_streak: number;
  reputation: number;
  tracks_in_progress: number;
  tracks_released: number;
  completion_rate: number;
}

export interface AbletonSession {
  id: number;
  project_name: string;
  session_date: string;
  started_at: string;
  ended_at: string;
  duration_minutes: number;
  save_count: number;
  export_count: number;
  bpm: number | null;
  musical_key: string | null;
  track_count: number | null;
}

export interface ExportEvent {
  id: number;
  project_name: string | null;
  file_path: string;
  changed_from_prev: number;
  similarity_score: number | null;
  duration_seconds: number | null;
  exported_at: string;
}

export interface AppSettings {
  ableton_project_folder: string;
  ableton_export_folder: string;
  artist_name: string;
  artist_phone: string;
  quiet_hours_start: string;
  quiet_hours_end: string;
  quiet_days: string[];
  dnd_enabled: boolean;
  // Remote mode — when set, app fetches from the Mac API instead of local SQLite
  remote_url: string;
  api_token: string;
}

// ── Remote mode ───────────────────────────────────────────────────────────────
// When remote_url + api_token are set in localStorage, all data fetches go
// to the Mac's HTTP API (port 8086 via Cloudflare tunnel) instead of local SQLite.
// This is how the Windows desktop app connects back to the Mac.
// In browser mode, the API is same-origin so no remote URL is needed.

const REMOTE_URL_KEY = "arl_remote_url";
const REMOTE_TOKEN_KEY = "arl_api_token";

export function getRemoteConfig(): { url: string; token: string } | null {
  // In browser mode, always use same-origin fetch with the stored token
  if (!isTauri()) {
    const token = localStorage.getItem(REMOTE_TOKEN_KEY)?.trim();
    if (token) return { url: "", token };
    return null;
  }
  // Tauri mode: traditional remote URL + token
  const url = localStorage.getItem(REMOTE_URL_KEY)?.trim();
  const token = localStorage.getItem(REMOTE_TOKEN_KEY)?.trim();
  if (url && token) return { url: url.replace(/\/$/, ""), token };
  return null;
}

export function setRemoteConfig(url: string, token: string): void {
  if (url && token) {
    localStorage.setItem(REMOTE_URL_KEY, url.trim().replace(/\/$/, ""));
    localStorage.setItem(REMOTE_TOKEN_KEY, token.trim());
  } else {
    localStorage.removeItem(REMOTE_URL_KEY);
    localStorage.removeItem(REMOTE_TOKEN_KEY);
  }
}

// In the packaged Tauri app with no manually configured remote, POST endpoints
// that have no Tauri-native equivalent (e.g. /api/intake, which needs
// multipart upload + audio processing on the Python side) still need a base
// URL + bearer token. Bootstrap both from the local server's own /token
// endpoint, which is reachable without auth for exactly this purpose.
const LOCAL_API_URL = "http://localhost:8086";
let cachedLocalToken: Promise<string> | null = null;

async function fetchLocalToken(): Promise<string> {
  const res = await fetch(`${LOCAL_API_URL}/token`);
  if (!res.ok) throw new Error(`Failed to reach local API: HTTP ${res.status}`);
  const data = (await res.json()) as { token: string };
  return data.token;
}

export async function resolveApiAuth(): Promise<{ url: string; token: string }> {
  const cfg = getRemoteConfig();
  if (cfg) return cfg;
  if (isTauri()) {
    if (!cachedLocalToken) cachedLocalToken = fetchLocalToken();
    return { url: LOCAL_API_URL, token: await cachedLocalToken };
  }
  return { url: "", token: "" };
}

async function remoteGet<T>(path: string): Promise<T> {
  const cfg = getRemoteConfig();
  if (!cfg) throw new Error("No remote config");
  const res = await fetch(`${cfg.url}${path}`, {
    headers: { Authorization: `Bearer ${cfg.token}` },
  });
  if (!res.ok) throw new Error(`HTTP ${res.status} from ${path}`);
  return res.json() as Promise<T>;
}

// In browser mode, ALL data fetches use the remote (same-origin) path.
// In Tauri mode, fall back to local invoke if no remote config.
function shouldUseRemote(): boolean {
  if (!isTauri()) return true; // browser always uses fetch
  return !!getRemoteConfig();
}

export async function fetchTrackAudioBlob(trackId: number): Promise<Blob> {
  const cfg = getRemoteConfig();
  const res = await fetch(`${cfg?.url ?? ""}/track_audio?track_id=${trackId}`, {
    headers: cfg ? { Authorization: `Bearer ${cfg.token}` } : {},
  });
  if (!res.ok) throw new Error(`HTTP ${res.status} loading track audio`);
  return res.blob();
}

export async function fetchVoiceBlob(messageId: number): Promise<Blob> {
  const cfg = getRemoteConfig();
  const res = await fetch(`${cfg?.url ?? ""}/tts?message_id=${messageId}`, {
    headers: cfg ? { Authorization: `Bearer ${cfg.token}` } : {},
  });
  if (!res.ok) throw new Error(`HTTP ${res.status} loading voice audio`);
  return res.blob();
}

export async function transcribeAudio(blob: Blob): Promise<string> {
  const { url: baseUrl, token } = await resolveApiAuth();
  const form = new FormData();
  form.append("file", blob, "utterance.webm");
  const res = await fetch(`${baseUrl}/stt`, {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: form,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(text || `HTTP ${res.status} transcribing audio`);
  }
  const data = (await res.json()) as { text: string };
  return data.text;
}

// ── Public API — automatically uses remote or local ───────────────────────────

export async function getTracks(): Promise<Track[]> {
  if (shouldUseRemote()) return remoteGet<Track[]>("/tracks");
  return tauriInvoke("get_tracks");
}

export async function getFeedback(trackId: number): Promise<Feedback[]> {
  if (shouldUseRemote()) return remoteGet<Feedback[]>(`/feedback?track_id=${trackId}`);
  return tauriInvoke("get_feedback", { trackId });
}

export async function getProjects(): Promise<Project[]> {
  if (shouldUseRemote()) return remoteGet<Project[]>("/projects");
  return tauriInvoke("get_projects");
}

export async function getArtistProfile(): Promise<ArtistProfile | null> {
  if (shouldUseRemote()) return remoteGet<ArtistProfile | null>("/artist_profile");
  return tauriInvoke("get_artist_profile");
}

export async function getReleaseStates(trackId: number): Promise<ReleaseStateEntry[]> {
  if (shouldUseRemote()) return remoteGet<ReleaseStateEntry[]>(`/release_states?track_id=${trackId}`);
  return tauriInvoke("get_release_states", { trackId });
}

export async function getStats(): Promise<Stats> {
  if (shouldUseRemote()) return remoteGet<Stats>("/stats");
  return tauriInvoke("get_stats");
}

export async function getSessions(): Promise<AbletonSession[]> {
  if (shouldUseRemote()) return remoteGet<AbletonSession[]>("/sessions");
  return tauriInvoke("get_sessions");
}

export async function getExportEvents(limit?: number): Promise<ExportEvent[]> {
  if (shouldUseRemote()) {
    const q = limit !== undefined ? `?limit=${limit}` : "";
    return remoteGet<ExportEvent[]>(`/export_events${q}`);
  }
  return tauriInvoke("get_export_events", limit !== undefined ? { limit } : {});
}

// Agent messaging — routes to the local Hermes API via /artist_message.
export async function sendAgentMessage(
  agent: string,
  message: string,
  trackId: number | null,
): Promise<Feedback> {
  if (shouldUseRemote()) {
    const cfg = getRemoteConfig();
    const res = await fetch(`${cfg?.url ?? ""}/artist_message`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(cfg ? { Authorization: `Bearer ${cfg.token}` } : {}),
      },
      body: JSON.stringify({ agent, message, track_id: trackId }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status} sending artist message`);
    return res.json() as Promise<Feedback>;
  }
  if (!isTauri()) {
    throw new Error("Agent messaging is only available in the desktop app (Mac)");
  }
  const { url: baseUrl, token } = await resolveApiAuth();
  const res = await fetch(`${baseUrl}/artist_message`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({ agent, message, track_id: trackId }),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status} sending artist message`);
  return res.json() as Promise<Feedback>;
}

export async function transitionTrackState(
  trackId: number,
  toState: string,
): Promise<Track> {
  if (shouldUseRemote()) {
    const cfg = getRemoteConfig();
    const res = await fetch(`${cfg?.url ?? ""}/release_states`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(cfg ? { Authorization: `Bearer ${cfg.token}` } : {}),
      },
      body: JSON.stringify({
        track_id: trackId,
        to_state: toState,
        changed_by: "artist",
        reason: "Updated from Deal Board",
      }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status} updating track state`);
    return res.json() as Promise<Track>;
  }
  return tauriInvoke("transition_track_state", { trackId, toState });
}

async function remotePost<T>(path: string, body: Record<string, unknown>): Promise<T> {
  const cfg = getRemoteConfig();
  const res = await fetch(`${cfg?.url ?? ""}${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(cfg ? { Authorization: `Bearer ${cfg.token}` } : {}),
    },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status} from ${path}`);
  return res.json() as Promise<T>;
}

export async function vaultTrack(trackId: number, reason = "Moved to vault"): Promise<Track> {
  if (shouldUseRemote()) {
    return remotePost<Track>("/tracks/vault", { track_id: trackId, reason });
  }
  throw new Error("Vault requires the Mac API connection");
}

export async function deleteTrackTracking(trackId: number): Promise<{ deleted: boolean; track_id: number }> {
  if (shouldUseRemote()) {
    return remotePost<{ deleted: boolean; track_id: number }>("/tracks/delete", {
      track_id: trackId,
      delete_file: false,
    });
  }
  throw new Error("Delete tracking requires the Mac API connection");
}

// ── Roundtable verdicts ────────────────────────────────────────────────────

export async function getVerdict(trackId: number): Promise<Verdict | null> {
  if (!shouldUseRemote()) {
    throw new Error("Verdicts require the Mac API connection");
  }
  const result = await remoteGet<{ verdict: Verdict | null }>(`/verdict?track_id=${trackId}`);
  return result.verdict;
}

export async function synthesizeVerdict(trackId: number): Promise<Verdict> {
  if (!shouldUseRemote()) {
    throw new Error("Verdicts require the Mac API connection");
  }
  const result = await remotePost<{ verdict: Verdict }>("/verdict/synthesize", {
    track_id: trackId,
  });
  return result.verdict;
}

export async function actOnVerdict(
  trackId: number,
  verdictId: number,
): Promise<{ track: Track; wave_vault_added: number }> {
  if (!shouldUseRemote()) {
    throw new Error("Verdicts require the Mac API connection");
  }
  return remotePost<{ track: Track; wave_vault_added: number }>("/verdict/act", {
    track_id: trackId,
    verdict_id: verdictId,
  });
}

// ── Track segments (granular analysis) ─────────────────────────────────────

export interface TrackSegment {
  id: number;
  start_sec: number;
  end_sec: number;
  section_label: string | null;
  energy: number | null;
  elements_present: string[];
  mood: string | null;
  production_notes: string | null;
  standout: boolean;
  standout_reason: string | null;
  visual_anchor: string | null;
  model_used: string | null;
  analyzed_at: string;
}

export async function getSegments(trackId: number): Promise<TrackSegment[]> {
  if (!shouldUseRemote()) {
    throw new Error("Segments require the Mac API connection");
  }
  const result = await remoteGet<{ segments: TrackSegment[] }>(
    `/segments?track_id=${trackId}`,
  );
  return result.segments;
}

// ── Audio features ────────────────────────────────────────────────────────

export interface AudioFeatures {
  id: number;
  track_id: number;
  bpm: number | null;
  beat_count: number | null;
  time_signature: string | null;
  musical_key: string | null;
  key_confidence: number | null;
  spectral_centroid_mean: number | null;
  spectral_rolloff_mean: number | null;
  loudness_rms: number | null;
  dynamic_range_db: number | null;
  analyzed_at: string;
}

export async function getAudioFeatures(trackId: number): Promise<AudioFeatures | null> {
  if (!shouldUseRemote()) return null;
  try {
    const result = await remoteGet<{ features: AudioFeatures }>(
      `/audio_features?track_id=${trackId}`,
    );
    return result.features;
  } catch {
    return null;
  }
}

// ── Insights graph ────────────────────────────────────────────────────────

export interface GraphNode {
  id: string;
  type: "track" | "mood" | "element" | "key" | "bpm" | "section"
      | "genre" | "instrument" | "subgenre" | "agent" | "verdict"
      | "mode" | "energy_level" | "rhythm_feel" | "texture";
  label: string;
}

export interface GraphLink {
  source: string;
  target: string;
}

export interface InsightsGraph {
  nodes: GraphNode[];
  links: GraphLink[];
}

export async function getInsightsGraph(): Promise<InsightsGraph> {
  if (!shouldUseRemote()) {
    throw new Error("Insights graph requires the Mac API connection");
  }
  return remoteGet<InsightsGraph>("/insights/graph");
}

// ── Artwork generations (Maren's NanoBanana variants) ──────────────────────

export interface ArtworkGeneration {
  id: number;
  track_id: number;
  brief: string;
  prompt: string;
  variant_axis: string | null;
  rationale: string | null;
  model: string;
  image_url: string | null;
  picked: number;
  created_at: string;
}

export async function getArtworkGenerations(trackId: number): Promise<ArtworkGeneration[]> {
  if (!shouldUseRemote()) {
    throw new Error("Artwork requires the Mac API connection");
  }
  const result = await remoteGet<{ generations: ArtworkGeneration[] }>(
    `/artwork/generations?track_id=${trackId}`,
  );
  return result.generations;
}

export async function generateArtwork(trackId: number): Promise<ArtworkGeneration[]> {
  if (!shouldUseRemote()) {
    throw new Error("Artwork requires the Mac API connection");
  }
  const result = await remotePost<{ generations: ArtworkGeneration[] }>(
    "/artwork/generate",
    { track_id: trackId },
  );
  return result.generations;
}

export async function pickArtwork(generationId: number): Promise<ArtworkGeneration> {
  if (!shouldUseRemote()) {
    throw new Error("Artwork requires the Mac API connection");
  }
  const result = await remotePost<{ generation: ArtworkGeneration }>(
    "/artwork/pick",
    { generation_id: generationId },
  );
  return result.generation;
}

// ── Wave Vault ─────────────────────────────────────────────────────────────

export interface WaveVaultEntry {
  id: number;
  track_id: number;
  track_title: string | null;
  stem: string;
  start_sec: number | null;
  end_sec: number | null;
  bpm: number | null;
  musical_key: string | null;
  tags: string[];
  notes: string | null;
  added_by: string | null;
  added_at: string;
}

export async function getWaveVault(): Promise<WaveVaultEntry[]> {
  if (!shouldUseRemote()) {
    throw new Error("Wave Vault requires the Mac API connection");
  }
  const result = await remoteGet<{ entries: WaveVaultEntry[] }>("/wave_vault");
  return result.entries;
}

export async function handleFileDrop(filePath: string): Promise<string> {
  if (!isTauri()) {
    throw new Error("File drop is only available in the desktop app (Mac)");
  }
  return tauriInvoke("handle_file_drop", { filePath });
}

// Path queries — descriptive strings in browser mode
export async function getDataDir(): Promise<string> {
  if (!isTauri()) return "via web";
  if (getRemoteConfig()) return "remote";
  return tauriInvoke("get_data_dir");
}

export async function getDbPath(): Promise<string> {
  if (!isTauri()) return "remote (Mac)";
  if (getRemoteConfig()) return "remote (Mac)";
  return tauriInvoke("get_db_path");
}

export async function getInboxDir(): Promise<string> {
  if (!isTauri()) return "remote (Mac)";
  if (getRemoteConfig()) return "remote (Mac)";
  return tauriInvoke("get_inbox_dir");
}

// Settings — in browser mode, POST/GET to /settings on the API server.
// In Tauri mode, always local (per-device config via Rust backend).
export async function saveSettings(settings: AppSettings): Promise<void> {
  // Persist remote config to localStorage separately so it survives settings reloads
  setRemoteConfig(settings.remote_url ?? "", settings.api_token ?? "");
  // Strip remote fields before saving (they're localStorage-only)
  const { remote_url: _r, api_token: _a, ...localSettings } = settings;

  if (!isTauri()) {
    const cfg = getRemoteConfig();
    const res = await fetch("/settings", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(cfg ? { Authorization: `Bearer ${cfg.token}` } : {}),
      },
      body: JSON.stringify(localSettings),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status} saving settings`);
    return;
  }

  return tauriInvoke("save_settings", { settings: localSettings });
}

export async function loadSettings(): Promise<AppSettings> {
  let local: Omit<AppSettings, "remote_url" | "api_token">;

  if (!isTauri()) {
    const cfg = getRemoteConfig();
    const res = await fetch("/settings", {
      headers: cfg ? { Authorization: `Bearer ${cfg.token}` } : {},
    });
    if (!res.ok) throw new Error(`HTTP ${res.status} loading settings`);
    local = await res.json();
  } else {
    local = await tauriInvoke<Omit<AppSettings, "remote_url" | "api_token">>("load_settings");
  }

  const cfg = getRemoteConfig();
  return {
    ...local,
    remote_url: cfg?.url ?? "",
    api_token: cfg?.token ?? "",
  };
}
