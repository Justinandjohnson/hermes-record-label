import { useCallback, useEffect, useRef, useState } from "react";
import type { Feedback } from "../lib/hermes-bridge";
import { sendAgentMessage, transcribeAudio } from "../lib/hermes-bridge";
import { playToCompletion, stopVoicePlayback } from "../lib/voice-playback";
import { stopTrackPlayback } from "../lib/track-playback";
import { startVadListening } from "../lib/vad-recorder";
import type { VadDiagnostics, VadSession } from "../lib/vad-recorder";
import type { PhaseInfo } from "../lib/pipeline-phase";

export type LiveModeMicState =
  | "off"
  | "waiting-round"
  | "agents-speaking"
  | "requesting-mic"
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
  micLevel: number;
  micDiagnostics: VadDiagnostics | null;
  selectedMicId: string | null;
  selectMicrophone: (deviceId: string) => void;
  micGain: number;
  setMicGain: (gain: number) => void;
  toggle: () => void;
  retry: () => void;
  sendTextReply: (message: string) => Promise<void>;
}

interface Args {
  trackId: number | null;
  outboundStream: Feedback[];
  phaseInfo: PhaseInfo | null;
  onMessageSent?: () => void | Promise<void>;
}

const POLL_MS = 500;
const REPLY_TARGET_AGENT = "a_and_r";
const MIC_DEVICE_STORAGE_KEY = "label-live-mic-device";
const MIC_GAIN_STORAGE_KEY = "label-live-mic-gain";

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export function useLiveMode({
  trackId,
  outboundStream,
  phaseInfo,
  onMessageSent,
}: Args): UseLiveModeResult {
  const [enabled, setEnabled] = useState(false);
  const [micState, setMicState] = useState<LiveModeMicState>("off");
  const [error, setError] = useState<string | null>(null);
  const [lastTranscript, setLastTranscript] = useState<string | null>(null);
  const [micLevel, setMicLevel] = useState(0);
  const [micDiagnostics, setMicDiagnostics] = useState<VadDiagnostics | null>(null);
  const [selectedMicId, setSelectedMicId] = useState<string | null>(() =>
    window.localStorage.getItem(MIC_DEVICE_STORAGE_KEY),
  );
  const [micGain, setMicGainState] = useState(() => {
    const saved = Number(window.localStorage.getItem(MIC_GAIN_STORAGE_KEY));
    return Number.isFinite(saved) && saved >= 0.25 && saved <= 4 ? saved : 1;
  });
  const micGainRef = useRef(micGain);
  micGainRef.current = micGain;
  const [retryTick, setRetryTick] = useState(0);

  const voicedIdsRef = useRef<Set<number>>(new Set());
  const queueRef = useRef<number[]>([]);
  const sessionKeyRef = useRef<string | null>(null);
  const vadSessionRef = useRef<VadSession | null>(null);
  const phaseInfoRef = useRef<PhaseInfo | null>(phaseInfo);
  const awaitingReplyRef = useRef(false);
  phaseInfoRef.current = phaseInfo;

  const stopMic = useCallback(() => {
    if (vadSessionRef.current) {
      vadSessionRef.current.cancel();
      vadSessionRef.current = null;
    }
    setMicLevel(0);
  }, []);

  // Seed/grow the auto-voice queue as new agent messages arrive. On first
  // activation for a track, speak only the latest existing table message so
  // Live Mode opens like a conversation instead of silently opening the mic.
  useEffect(() => {
    if (!enabled || trackId === null) return;
    const key = String(trackId);
    if (sessionKeyRef.current !== key) {
      sessionKeyRef.current = key;
      voicedIdsRef.current = new Set(outboundStream.map((m) => m.id));
      const latest = outboundStream[outboundStream.length - 1];
      queueRef.current = latest ? [latest.id] : [];
      return;
    }
    let queuedReply = false;
    for (const msg of outboundStream) {
      if (!voicedIdsRef.current.has(msg.id)) {
        voicedIdsRef.current.add(msg.id);
        queueRef.current.push(msg.id);
        queuedReply = true;
      }
    }
    if (queuedReply) {
      awaitingReplyRef.current = false;
      stopMic();
    }
  }, [enabled, outboundStream, stopMic, trackId]);

  useEffect(() => {
    if (!enabled) {
      sessionKeyRef.current = null;
      awaitingReplyRef.current = false;
    }
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
            stopTrackPlayback();
            await playToCompletion(id);
          } catch (err) {
            if (cancelled) return;
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

        if (awaitingReplyRef.current) {
          setMicState("waiting-reply");
          await sleep(POLL_MS);
          continue;
        }

        // 3. Listen for the artist's spoken reply (VAD, no push-to-talk).
        setMicState("requesting-mic");
        const session = startVadListening({
          deviceId: selectedMicId ?? undefined,
          inputGain: () => micGainRef.current,
          onReady: () => {
            if (!cancelled) setMicState("listening");
          },
          onSpeechStart: () => {
            if (!cancelled) setMicState("recording");
          },
          onLevel: (level) => {
            if (!cancelled) setMicLevel(level);
          },
          onDiagnostics: (diagnostics) => {
            if (!cancelled) {
              setMicDiagnostics(diagnostics);
              setSelectedMicId((current) =>
                current && diagnostics.availableInputs.some((input) => input.deviceId === current)
                  ? current
                  : diagnostics.deviceId,
              );
            }
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
          if (message === "Listening cancelled") continue;
          if (message === "No speech captured") continue; // benign — re-arm
          fail(message); // real permission/hardware failure
          return;
        }
        vadSessionRef.current = null;
        setMicLevel(0);
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
        if (/^\s*[\[(]?(static|noise|silence|inaudible)[\])]?\s*[.!]?\s*$/i.test(transcript)) {
          fail("Only static was captured. Choose a different microphone input and retry.");
          return;
        }

        setLastTranscript(transcript);

        // 5. Submit into the round table.
        setMicState("submitting");
        try {
          await sendAgentMessage(REPLY_TARGET_AGENT, transcript, trackId);
          await onMessageSent?.();
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
      stopVoicePlayback();
    };
  }, [enabled, onMessageSent, retryTick, selectedMicId, stopMic, trackId]);

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

  const selectMicrophone = useCallback((deviceId: string) => {
    setSelectedMicId(deviceId);
    window.localStorage.setItem(MIC_DEVICE_STORAGE_KEY, deviceId);
    setError(null);
  }, []);

  const setMicGain = useCallback((gain: number) => {
    const next = Math.max(0.25, Math.min(4, gain));
    micGainRef.current = next;
    setMicGainState(next);
    window.localStorage.setItem(MIC_GAIN_STORAGE_KEY, String(next));
  }, []);

  useEffect(() => {
    if (selectedMicId) window.localStorage.setItem(MIC_DEVICE_STORAGE_KEY, selectedMicId);
  }, [selectedMicId]);

  const sendTextReply = useCallback(
    async (message: string) => {
      if (trackId === null) throw new Error("Select a track before replying");
      awaitingReplyRef.current = true;
      stopMic();
      setError(null);
      setMicState("submitting");
      try {
        await sendAgentMessage(REPLY_TARGET_AGENT, message, trackId);
        await onMessageSent?.();
        setMicState("waiting-reply");
      } catch (err) {
        awaitingReplyRef.current = false;
        const messageText = err instanceof Error ? err.message : "Failed to send message";
        setError(messageText);
        setMicState("error");
        throw err;
      }
    },
    [onMessageSent, stopMic, trackId],
  );

  return {
    enabled,
    micState,
    error,
    lastTranscript,
    micLevel,
    micDiagnostics,
    selectedMicId,
    selectMicrophone,
    micGain,
    setMicGain,
    toggle,
    retry,
    sendTextReply,
  };
}
