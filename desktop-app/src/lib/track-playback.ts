import { fetchTrackAudioBlob } from "./hermes-bridge";

type PlaybackSnapshot = {
  trackId: number | null;
  playing: boolean;
  loading: boolean;
  error: string | null;
};

export type PlaybackPositionSnapshot = {
  active: boolean;
  currentTime: number;
  duration: number;
  looping: boolean;
};

type Listener = (snapshot: PlaybackSnapshot) => void;
type PositionListener = (snapshot: PlaybackPositionSnapshot) => void;

const audio = new Audio();
export function getTrackAudioElement(): HTMLAudioElement {
  return audio;
}
const listeners = new Set<Listener>();
const positionListeners = new Set<PositionListener>();
/** Object URLs are revoked (oldest first) beyond this cap so long sessions don't leak memory. */
const URL_CACHE_LIMIT = 12;
const urlCache = new Map<number, string>();

let activeTrackId: number | null = null;
let loadingTrackId: number | null = null;
let lastError: string | null = null;

function snapshot(): PlaybackSnapshot {
  return {
    trackId: activeTrackId,
    playing: !audio.paused && !audio.ended,
    loading: loadingTrackId !== null,
    error: lastError,
  };
}

function positionSnapshot(): PlaybackPositionSnapshot {
  return {
    active: activeTrackId !== null,
    currentTime: audio.currentTime || 0,
    duration: Number.isFinite(audio.duration) ? audio.duration : 0,
    looping: audio.loop,
  };
}

function emit(): void {
  const next = snapshot();
  for (const listener of listeners) {
    listener(next);
  }
}

function emitPosition(): void {
  const next = positionSnapshot();
  for (const listener of positionListeners) {
    listener(next);
  }
}

audio.addEventListener("play", emit);
audio.addEventListener("pause", () => {
  emit();
  emitPosition();
});
// timeupdate (~4 Hz native cadence) only feeds the player bar, not every button.
audio.addEventListener("timeupdate", emitPosition);
audio.addEventListener("durationchange", emitPosition);
audio.addEventListener("loadedmetadata", emitPosition);
audio.addEventListener("ended", () => {
  activeTrackId = null;
  emit();
  emitPosition();
});
audio.addEventListener("error", () => {
  lastError = "Playback failed";
  loadingTrackId = null;
  activeTrackId = null;
  emit();
});

function cacheUrl(trackId: number, url: string): void {
  // Refresh for LRU ordering.
  urlCache.delete(trackId);
  urlCache.set(trackId, url);
  if (urlCache.size > URL_CACHE_LIMIT) {
    const oldest = urlCache.keys().next().value;
    if (oldest !== undefined && oldest !== trackId && oldest !== activeTrackId) {
      const stale = urlCache.get(oldest);
      urlCache.delete(oldest);
      if (stale && audio.src !== stale) URL.revokeObjectURL(stale);
    }
  }
}

async function resolveAudioUrl(trackId: number): Promise<string> {
  const cached = urlCache.get(trackId);
  if (cached) return cached;
  const blob = await fetchTrackAudioBlob(trackId);
  const url = URL.createObjectURL(blob);
  cacheUrl(trackId, url);
  return url;
}

export function subscribePlayback(listener: Listener): () => void {
  listeners.add(listener);
  listener(snapshot());
  return () => listeners.delete(listener);
}

/**
 * Position stream for player surfaces (progress bar, loop state, markers).
 * Kept separate from subscribePlayback so per-card buttons don't re-render
 * on every timeupdate tick.
 */
export function subscribePlaybackPosition(listener: PositionListener): () => void {
  positionListeners.add(listener);
  listener(positionSnapshot());
  return () => positionListeners.delete(listener);
}

/** Current playback position of the loaded track, or null when nothing is active. */
export function getActivePlaybackTime(): number | null {
  if (activeTrackId === null) return null;
  return audio.currentTime || 0;
}

export async function preloadTrackAudio(trackId: number): Promise<void> {
  if (activeTrackId === trackId && audio.src) return;
  try {
    const url = await resolveAudioUrl(trackId);
    if (activeTrackId === null || activeTrackId !== trackId) {
      audio.src = url;
      activeTrackId = trackId;
      emitPosition();
    }
  } catch {
    // Non-fatal preload
  }
}

export function seekTrackPlayback(seconds: number): void {
  if (!Number.isFinite(seconds)) return;
  const max = Number.isFinite(audio.duration) ? audio.duration : seconds;
  audio.currentTime = Math.max(0, Math.min(seconds, max));
  emitPosition();
}

/** Jump back to 0 and play. */
export function restartTrackPlayback(): void {
  if (activeTrackId === null) return;
  audio.currentTime = 0;
  void audio.play().catch(() => undefined);
  emitPosition();
}

export function setTrackLoop(loop: boolean): void {
  audio.loop = loop;
  emitPosition();
}

export async function toggleTrackPlayback(trackId: number): Promise<void> {
  lastError = null;
  if (activeTrackId === trackId && !audio.paused) {
    audio.pause();
    return;
  }
  loadingTrackId = trackId;
  emit();
  try {
    const url = await resolveAudioUrl(trackId);
    if (activeTrackId !== trackId || audio.src !== url) {
      audio.src = url;
      activeTrackId = trackId;
      emitPosition();
    }
    await audio.play();
  } catch (error) {
    activeTrackId = null;
    lastError = error instanceof Error ? error.message : "Playback failed";
  } finally {
    loadingTrackId = null;
    emit();
  }
}

export function stopTrackPlayback(): void {
  audio.pause();
  audio.currentTime = 0;
  activeTrackId = null;
  loadingTrackId = null;
  emit();
  emitPosition();
}
