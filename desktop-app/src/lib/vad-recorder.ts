/**
 * Client-side voice activity detection + recording for Live Mode.
 *
 * No push-to-talk: opens the mic, watches RMS amplitude via the Web Audio
 * API to detect speech onset/offset, and records only the utterance itself
 * via MediaRecorder. Resolves a single audio Blob once silence is detected.
 */

export interface VadOptions {
  /** RMS (0..1) above which audio is considered speech. */
  speechRmsThreshold?: number;
  /** Silence duration that ends an utterance. */
  silenceTimeoutMs?: number;
  /** Hard cap so a stuck-open mic can't record forever. */
  maxRecordingMs?: number;
  /** Utterances shorter than this are treated as noise, not a reply. */
  minRecordingMs?: number;
  /** Fired the moment speech onset is detected and recording actually starts. */
  onSpeechStart?: () => void;
}

export interface VadSession {
  /** Resolves with the captured utterance once silence ends it. */
  promise: Promise<Blob>;
  /** Stop listening/recording immediately and release the microphone. */
  cancel: () => void;
}

const DEFAULT_OPTS: Required<Omit<VadOptions, "onSpeechStart">> = {
  speechRmsThreshold: 0.02,
  silenceTimeoutMs: 1200,
  maxRecordingMs: 20000,
  minRecordingMs: 300,
};

const RECORDER_MIME_CANDIDATES = ["audio/mp4", "audio/webm", "audio/ogg"];

function pickRecorderMimeType(): string {
  for (const type of RECORDER_MIME_CANDIDATES) {
    if (typeof MediaRecorder !== "undefined" && MediaRecorder.isTypeSupported(type)) {
      return type;
    }
  }
  throw new Error("No supported audio recording format available in this browser");
}

export function startVadListening(opts: VadOptions = {}): VadSession {
  const cfg = { ...DEFAULT_OPTS, ...opts };
  let cancelled = false;
  let stream: MediaStream | null = null;
  let audioCtx: AudioContext | null = null;
  let rafId: number | null = null;
  let recorder: MediaRecorder | null = null;
  let silenceTimer: ReturnType<typeof setTimeout> | null = null;
  let maxTimer: ReturnType<typeof setTimeout> | null = null;
  let recordingStartedAt = 0;

  const teardown = () => {
    if (rafId !== null) cancelAnimationFrame(rafId);
    if (silenceTimer !== null) clearTimeout(silenceTimer);
    if (maxTimer !== null) clearTimeout(maxTimer);
    rafId = null;
    silenceTimer = null;
    maxTimer = null;
    if (audioCtx) {
      audioCtx.close().catch(() => undefined);
      audioCtx = null;
    }
    if (stream) {
      for (const track of stream.getTracks()) track.stop();
      stream = null;
    }
  };

  let cancelFn: () => void = () => {
    cancelled = true;
  };

  const promise = new Promise<Blob>((resolve, reject) => {
    cancelFn = () => {
      if (cancelled) return;
      cancelled = true;
      if (recorder && recorder.state !== "inactive") recorder.stop();
      teardown();
      reject(new Error("Listening cancelled"));
    };

    (async () => {
      let acquiredStream: MediaStream;
      try {
        acquiredStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      } catch (err) {
        reject(err instanceof Error ? err : new Error("Microphone permission denied"));
        return;
      }
      if (cancelled) {
        for (const track of acquiredStream.getTracks()) track.stop();
        return;
      }
      stream = acquiredStream;

      let mimeType: string;
      try {
        mimeType = pickRecorderMimeType();
      } catch (err) {
        teardown();
        reject(err instanceof Error ? err : new Error("No supported recording format"));
        return;
      }

      audioCtx = new AudioContext();
      const source = audioCtx.createMediaStreamSource(stream);
      const analyser = audioCtx.createAnalyser();
      analyser.fftSize = 2048;
      source.connect(analyser);
      const timeData = new Float32Array(analyser.fftSize);

      const chunks: BlobPart[] = [];
      recorder = new MediaRecorder(stream, { mimeType });
      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunks.push(e.data);
      };
      recorder.onstop = () => {
        const durationMs = Date.now() - recordingStartedAt;
        teardown();
        if (cancelled) return;
        if (durationMs < cfg.minRecordingMs || chunks.length === 0) {
          reject(new Error("No speech captured"));
          return;
        }
        resolve(new Blob(chunks, { type: mimeType }));
      };

      let speaking = false;

      const armSilenceTimer = () => {
        if (silenceTimer !== null) clearTimeout(silenceTimer);
        silenceTimer = setTimeout(() => {
          if (recorder && recorder.state === "recording") recorder.stop();
        }, cfg.silenceTimeoutMs);
      };

      const tick = () => {
        if (cancelled || !audioCtx) return;
        analyser.getFloatTimeDomainData(timeData);
        let sumSquares = 0;
        for (let i = 0; i < timeData.length; i++) sumSquares += timeData[i] * timeData[i];
        const rms = Math.sqrt(sumSquares / timeData.length);

        if (rms >= cfg.speechRmsThreshold) {
          if (!speaking) {
            speaking = true;
            recordingStartedAt = Date.now();
            recorder!.start();
            opts.onSpeechStart?.();
            maxTimer = setTimeout(() => {
              if (recorder && recorder.state === "recording") recorder.stop();
            }, cfg.maxRecordingMs);
          }
          armSilenceTimer();
        }

        rafId = requestAnimationFrame(tick);
      };
      rafId = requestAnimationFrame(tick);
    })();
  });

  return {
    promise,
    cancel: () => cancelFn(),
  };
}
