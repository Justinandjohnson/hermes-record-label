import { fetchVoiceBlob } from "./hermes-bridge";

type PlaybackSnapshot = {
  messageId: number | null;
  playing: boolean;
  loading: boolean;
  error: string | null;
};

type Listener = (snapshot: PlaybackSnapshot) => void;

const audio = new Audio();
const listeners = new Set<Listener>();
const urlCache = new Map<number, string>();

let activeMessageId: number | null = null;
let loadingMessageId: number | null = null;
let lastError: string | null = null;
let pendingCompletion: { messageId: number; resolve: () => void; reject: (err: Error) => void } | null = null;
let playbackGeneration = 0;

function snapshot(): PlaybackSnapshot {
  return {
    messageId: activeMessageId,
    playing: !audio.paused && !audio.ended,
    loading: loadingMessageId !== null,
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
  activeMessageId = null;
  if (pendingCompletion) {
    const { resolve } = pendingCompletion;
    pendingCompletion = null;
    resolve();
  }
  emit();
});
audio.addEventListener("error", () => {
  lastError = "Voice playback failed";
  loadingMessageId = null;
  activeMessageId = null;
  if (pendingCompletion) {
    const { reject } = pendingCompletion;
    pendingCompletion = null;
    reject(new Error(lastError));
  }
  emit();
});

async function resolveAudioUrl(messageId: number): Promise<string> {
  const cached = urlCache.get(messageId);
  if (cached) return cached;
  const blob = await fetchVoiceBlob(messageId);
  const url = URL.createObjectURL(blob);
  urlCache.set(messageId, url);
  return url;
}

export function subscribeVoicePlayback(listener: Listener): () => void {
  listeners.add(listener);
  listener(snapshot());
  return () => listeners.delete(listener);
}

async function startPlayback(messageId: number, generation = playbackGeneration): Promise<void> {
  const url = await resolveAudioUrl(messageId);
  if (generation !== playbackGeneration) throw new Error("Voice playback cancelled");
  if (activeMessageId !== messageId || audio.src !== url) {
    audio.src = url;
    activeMessageId = messageId;
  }
  await audio.play();
}

export async function toggleVoicePlayback(messageId: number): Promise<void> {
  lastError = null;
  if (activeMessageId === messageId && !audio.paused) {
    audio.pause();
    return;
  }
  stopVoicePlayback();
  loadingMessageId = messageId;
  const generation = playbackGeneration;
  emit();
  try {
    await startPlayback(messageId, generation);
  } catch (error) {
    activeMessageId = null;
    lastError = error instanceof Error ? error.message : "Voice playback failed";
  } finally {
    loadingMessageId = null;
    emit();
  }
}

/**
 * Play a message's voice and resolve once it finishes (or reject on failure).
 * Used by Live Mode to sequence auto-playback of new agent messages.
 */
export function playToCompletion(messageId: number): Promise<void> {
  stopVoicePlayback();
  const generation = playbackGeneration;
  lastError = null;
  loadingMessageId = messageId;
  emit();
  return new Promise<void>((resolve, reject) => {
    pendingCompletion = { messageId, resolve, reject };
    startPlayback(messageId, generation)
      .then(() => {
        loadingMessageId = null;
        emit();
      })
      .catch((error: unknown) => {
        pendingCompletion = null;
        loadingMessageId = null;
        activeMessageId = null;
        lastError = error instanceof Error ? error.message : "Voice playback failed";
        emit();
        reject(error instanceof Error ? error : new Error(lastError));
      });
  });
}

/** Immediately cancel any loading or playing agent voice. */
export function stopVoicePlayback(): void {
  playbackGeneration += 1;
  audio.pause();
  audio.currentTime = 0;
  loadingMessageId = null;
  activeMessageId = null;
  if (pendingCompletion) {
    const { reject } = pendingCompletion;
    pendingCompletion = null;
    reject(new Error("Voice playback cancelled"));
  }
  emit();
}
