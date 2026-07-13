import { useCallback, useEffect, useRef, useState } from "react";
import type { Feedback } from "../lib/hermes-bridge";
import { sendAgentMessage, transcribeAudio } from "../lib/hermes-bridge";
import { playToCompletion } from "../lib/voice-playback";
import { startVadListening } from "../lib/vad-recorder";
import type { VadSession } from "../lib/vad-recorder";
import type { PhaseInfo } from "../lib/pipeline-phase";

export type LiveModeMicState =
  | "off"
  | "waiting-round"
  | "agents-speaking"
  | "listening"
  | "recording"
  | "transcribing"
  | "submitting"
  | "waiting-reply"
  | "error";

export interface UseLiveModeResult {
  enabled: boolean;
  micState: LiveModeMicState;
  error: string | null;
  lastTranscript: string | null;
  toggle: () => void;
  retry: () => void;
}

interface Args {
  trackId: number | null;
  outboundStream: Feedback[];
  phaseInfo: PhaseInfo | null;
}

const POLL_MS = 500;
const REPLY_TARGET_AGENT = "a_and_r";

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export function useLiveMode({ trackId, outboundStream, phaseInfo }: Args): UseLiveModeResult {
  const [enabled, setEnabled] = useState(false);
  const [micState, setMicState] = useState<LiveModeMicState>("off");
  const [error, setError] = useState<string | null>(null);
  const [lastTranscript, setLastTranscript] = useState<string | null>(null);
  const [retryTick, setRetryTick] = useState(0);

  const voicedIdsRef = useRef<Set<number>>(new Set());
  const queueRef = useRef<number[]>([]);
  const sessionKeyRef = useRef<string | null>(null);
  const vadSessionRef = useRef<VadSession | null>(null);
  const phaseInfoRef = useRef<PhaseInfo | null>(phaseInfo);
  phaseInfoRef.current = phaseInfo;

  const stopMic = useCallback(() => {
    if (vadSessionRef.current) {
      vadSessionRef.current.cancel();
      vadSessionRef.current = null;
    }
  }, []);

  // Seed/grow the auto-voice queue as new agent messages arrive. On first
  // activation for a track, mark the existing backlog as already-voiced so
  // Live Mode only reads out what happens *after* it's switched on.
  useEffect(() => {
    if (!enabled || trackId === null) return;
    const key = String(trackId);
    if (sessionKeyRef.current !== key) {
      sessionKeyRef.current = key;
      voicedIdsRef.current = new Set(outboundStream.map((m) => m.id));
      queueRef.current = [];
      return;
    }
    for (const msg of outboundStream) {
      if (!voicedIdsRef.current.has(msg.id)) {
        voicedIdsRef.current.add(msg.id);
        queueRef.current.push(msg.id);
      }
    }
  }, [enabled, trackId, outboundStream]);

  useEffect(() => {
    if (!enabled) sessionKeyRef.current = null;
  }, [enabled]);

  // The orchestration loop: drain voiced messages, then listen, transcribe,
  // submit, and wait for the next round — repeat until disabled/errored.
  useEffect(() => {
    if (!enabled || trackId === null) {
      setMicState("off");
      return;
    }

    let cancelled = false;
    setError(null);

    const fail = (message: string) => {
      stopMic();
      setError(message);
      setMicState("error");
    };

    async function loop() {
      while (!cancelled) {
        // 1. Drain the auto-voice queue.
        if (queueRef.current.length > 0) {
          setMicState("agents-speaking");
          const id = queueRef.current.shift() as number;
          try {
            await playToCompletion(id);
          } catch (err) {
            fail(err instanceof Error ? err.message : "Voice playback failed");
            return;
          }
          continue;
        }

        // 2. Wait for the current review round to actually finish.
        const phase = phaseInfoRef.current;
        if (phase?.isPendingAgents || phase?.isAnalyzing) {
          setMicState("waiting-round");
          await sleep(POLL_MS);
          continue;
        }

        // 3. Listen for the artist's spoken reply (VAD, no push-to-talk).
        setMicState("listening");
        const session = startVadListening({
          onSpeechStart: () => {
            if (!cancelled) setMicState("recording");
          },
        });
        vadSessionRef.current = session;
        let blob: Blob;
        try {
          blob = await session.promise;
        } catch (err) {
          vadSessionRef.current = null;
          if (cancelled) return;
          const message = err instanceof Error ? err.message : "Microphone error";
          if (message === "Listening cancelled") return;
          if (message === "No speech captured") continue; // benign — re-arm
          fail(message); // real permission/hardware failure
          return;
        }
        vadSessionRef.current = null;
        if (cancelled) return;

        // 4. Transcribe.
        setMicState("transcribing");
        let transcript: string;
        try {
          transcript = await transcribeAudio(blob);
        } catch (err) {
          fail(err instanceof Error ? err.message : "Transcription failed");
          return;
        }
        if (cancelled) return;
        if (!transcript.trim()) continue; // nothing usable — keep listening

        setLastTranscript(transcript);

        // 5. Submit into the round table.
        setMicState("submitting");
        try {
          await sendAgentMessage(REPLY_TARGET_AGENT, transcript, trackId);
        } catch (err) {
          fail(err instanceof Error ? err.message : "Failed to send message");
          return;
        }
        if (cancelled) return;

        // 6. Wait for agents to respond (poll — next round picks it up).
        setMicState("waiting-reply");
        while (!cancelled && queueRef.current.length === 0) {
          const p = phaseInfoRef.current;
          if (p?.isPendingAgents || p?.isAnalyzing) break; // fall through to step 2
          await sleep(POLL_MS);
        }
      }
    }

    loop();

    return () => {
      cancelled = true;
      stopMic();
    };
  }, [enabled, trackId, retryTick, stopMic]);

  const toggle = useCallback(() => {
    setEnabled((e) => {
      const next = !e;
      if (!next) {
        setError(null);
        setLastTranscript(null);
      }
      return next;
    });
  }, []);

  const retry = useCallback(() => {
    setError(null);
    setMicState("off");
    setRetryTick((t) => t + 1);
  }, []);

  return { enabled, micState, error, lastTranscript, toggle, retry };
}
