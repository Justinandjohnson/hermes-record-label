import { fetchTrackAudioBlob } from "./hermes-bridge";

type PlaybackSnapshot = {
  trackId: number | null;
  playing: boolean;
  loading: boolean;
  error: string | null;
};

type Listener = (snapshot: PlaybackSnapshot) => void;

const audio = new Audio();
const listeners = new Set<Listener>();
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

function emit(): void {
  const next = snapshot();
  for (const listener of listeners) {
    listener(next);
  }
}

audio.addEventListener("play", emit);
audio.addEventListener("pause", emit);
audio.addEventListener("ended", () => {
  activeTrackId = null;
  emit();
});
audio.addEventListener("error", () => {
  lastError = "Playback failed";
  loadingTrackId = null;
  activeTrackId = null;
  emit();
});

async function resolveAudioUrl(trackId: number): Promise<string> {
  const cached = urlCache.get(trackId);
  if (cached) return cached;
  const blob = await fetchTrackAudioBlob(trackId);
  const url = URL.createObjectURL(blob);
  urlCache.set(trackId, url);
  return url;
}

export function subscribePlayback(listener: Listener): () => void {
  listeners.add(listener);
  listener(snapshot());
  return () => listeners.delete(listener);
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
