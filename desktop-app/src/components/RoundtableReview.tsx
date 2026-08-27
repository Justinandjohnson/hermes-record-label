import { useMemo, useState } from "react";
import { isAgentName } from "../hooks/useAgentMessages";
import type { AgentName } from "../hooks/useAgentMessages";
import { useLiveMode } from "../hooks/useLiveMode";
import type { LiveModeMicState } from "../hooks/useLiveMode";
import { useSegments } from "../hooks/useSegments";
import { useVerdict } from "../hooks/useVerdict";
import { kickOffDebate, sendAgentMessage } from "../lib/hermes-bridge";
import type { Feedback, Track } from "../lib/hermes-bridge";
import { getActivePlaybackTime } from "../lib/track-playback";
import { derivePipelinePhase } from "../lib/pipeline-phase";
import type { ReleaseState } from "../lib/state-machine";
import { AGENT_ORDER, SUMMARY_INTENTS, HIDDEN_INTENTS } from "../lib/agents";
import { VERDICT_META } from "../lib/verdict";
import ArtworkVariants from "./ArtworkVariants";
import RoundTableCanvas from "./RoundTableCanvas";
import SegmentTimeline from "./SegmentTimeline";

const LIVE_MODE_STATUS_LABEL: Record<LiveModeMicState, string> = {
  off: "Live Mode off",
  "waiting-round": "Waiting for agents to finish…",
  "agents-speaking": "Agents speaking…",
  "requesting-mic": "Connecting microphone…",
  listening: "Listening…",
  recording: "Recording…",
  transcribing: "Transcribing…",
  submitting: "Sending…",
  "waiting-reply": "Waiting for a reply…",
  error: "Live Mode error",
};

const ART_STATES = new Set([
  "ART_NEEDED",
  "ART_SUBMITTED",
  "ART_APPROVED",
  "RELEASE_READY",
  "PREFLIGHT",
  "UPLOADING",
]);

interface Props {
  track: Track | null;
  messages: Feedback[];
  title?: string;
  onMessagesChanged?: () => void | Promise<void>;
}

export default function RoundtableReview({
  track,
  messages,
  title = "Roundtable",
  onMessagesChanged,
}: Props) {
  const visibleMessages = useMemo(
    () =>
      messages
        .filter(
          (m) =>
            (m.direction === "inbound" || (m.direction === "outbound" && isAgentName(m.agent))) &&
            !HIDDEN_INTENTS.has(m.intent ?? ""),
        )
        .sort(
          (a, b) =>
            new Date(a.created_at).getTime() - new Date(b.created_at).getTime() || a.id - b.id,
        ),
    [messages],
  );

  const outboundStream = useMemo(
    () => visibleMessages.filter((m) => m.direction === "outbound" && isAgentName(m.agent)),
    [visibleMessages],
  );

  const outboundByAgent = useMemo(() => {
    const map = new Map<AgentName, Feedback[]>();
    for (const msg of outboundStream) {
      const list = map.get(msg.agent as AgentName) ?? [];
      list.push(msg);
      map.set(msg.agent as AgentName, list);
    }
    return map;
  }, [outboundStream]);

  const activeAgents = useMemo(
    () => AGENT_ORDER.filter((a) => (outboundByAgent.get(a) ?? []).length > 0),
    [outboundByAgent],
  );

  const summary = useMemo(
    () =>
      [...visibleMessages]
        .reverse()
        .find((m) => m.direction === "outbound" && SUMMARY_INTENTS.has(m.intent ?? "")) ?? null,
    [visibleMessages],
  );
  const latestArtistMessage = useMemo(
    () => [...visibleMessages].reverse().find((m) => m.direction === "inbound") ?? null,
    [visibleMessages],
  );

  const phaseInfo = track ? derivePipelinePhase(track.state as ReleaseState, messages) : null;

  const { verdict, acting, act } = useVerdict(track?.id ?? null);
  const { segments } = useSegments(track?.id ?? null);
  const liveMode = useLiveMode({
    trackId: track?.id ?? null,
    outboundStream,
    phaseInfo,
    onMessageSent: onMessagesChanged,
  });

  const [replyText, setReplyText] = useState("");
  const [replySending, setReplySending] = useState(false);
  const [replyError, setReplyError] = useState<string | null>(null);

  const handleSendReply = async () => {
    if (!track || !replyText.trim() || replySending) return;
    setReplySending(true);
    setReplyError(null);
    try {
      const message = replyText.trim();
      if (liveMode.enabled) {
        await liveMode.sendTextReply(message);
      } else {
        await sendAgentMessage("a_and_r", message, track.id, getActivePlaybackTime());
        await onMessagesChanged?.();
      }
      setReplyText("");
    } catch (err) {
      setReplyError(err instanceof Error ? err.message : "Failed to send message");
    } finally {
      setReplySending(false);
    }
  };

  if (!track) {
    return (
      <div className="card flex h-full min-h-0 items-center justify-center">
        <p className="text-sm text-zinc-600">Select a track to open the roundtable.</p>
      </div>
    );
  }

  const showArtwork = ART_STATES.has(track.state);

  return (
    <div className="card flex h-full min-h-0 flex-col overflow-hidden p-3">
      <div className="flex shrink-0 items-center justify-between gap-3 pb-2">
        <div className="min-w-0 flex-1">
          <p className="text-[10px] font-semibold uppercase tracking-wide text-zinc-500">{title}</p>
          <h3 className="mt-0.5 truncate text-sm font-semibold text-zinc-100">
            {track.title ?? "Untitled Track"}
          </h3>
          {segments.length > 0 && (
            <div className="mt-1.5 max-w-xs">
              <SegmentTimeline segments={segments} durationSec={track.duration_seconds} />
            </div>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {activeAgents.length > 0 && (
            <span className="rounded-full bg-surface-2 px-2 py-0.5 text-[10px] text-zinc-400">
              {activeAgents.length} active
            </span>
          )}
          {verdict ? (
            <span className="rounded-full bg-label-500/15 px-2 py-0.5 text-[10px] font-semibold text-label-300">
              Verdict: {VERDICT_META[verdict.recommendation].label}
            </span>
          ) : summary ? (
            <span className="rounded-full bg-blue-500/10 px-2 py-0.5 text-[10px] font-semibold text-blue-300">
              Conductor has a call
            </span>
          ) : null}
          <button
            type="button"
            onClick={() => {
              if (track.id != null) void kickOffDebate(track.id);
            }}
            title="Agents argue it out with each other — listen in and interject anytime"
            className="rounded-full bg-violet-500/15 px-2 py-0.5 text-[10px] font-semibold text-violet-300 transition-colors hover:bg-violet-500/25"
          >
            Let them talk
          </button>
          <button
            type="button"
            onClick={liveMode.toggle}
            title="Hands-free voice round table: agents auto-voice, mic auto-listens"
            className={`rounded-full px-2 py-0.5 text-[10px] font-semibold transition-colors ${
              liveMode.enabled
                ? "bg-emerald-500/20 text-emerald-300"
                : "bg-surface-2 text-zinc-400 hover:text-zinc-200"
            }`}
          >
            {liveMode.enabled ? "Live Mode: On" : "Live Mode"}
          </button>
        </div>
      </div>

      {liveMode.enabled && (
        <div className="mb-2 flex shrink-0 items-center gap-2 rounded-lg bg-surface-2 px-2.5 py-1.5 text-[10px]">
          <span className={liveMode.micState === "error" ? "text-red-400" : "text-zinc-400"}>
            {LIVE_MODE_STATUS_LABEL[liveMode.micState]}
          </span>
          {liveMode.error && (
            <>
              <span className="text-red-400">— {liveMode.error}</span>
              <button
                type="button"
                onClick={liveMode.retry}
                className="btn-ghost ml-auto px-2 py-0.5 text-[10px]"
              >
                Retry
              </button>
            </>
          )}
          {(liveMode.micState === "listening" || liveMode.micState === "recording") && (
            <>
              <div className="h-1.5 w-20 overflow-hidden rounded-full bg-surface-3" title="Microphone input level">
                <div
                  className={`h-full transition-[width] duration-100 ${
                    liveMode.micState === "recording" ? "bg-emerald-400" : "bg-label-400"
                  }`}
                  style={{ width: `${Math.max(3, liveMode.micLevel * 100)}%` }}
                />
              </div>
              <span className="text-zinc-500">
                {liveMode.micState === "recording" ? "Sends after 4.5s of silence" : "Speak naturally"}
              </span>
              {liveMode.micDiagnostics && (
                <div className="ml-auto flex min-w-0 items-center gap-1">
                  <select
                    aria-label="Microphone input"
                    value={liveMode.selectedMicId ?? liveMode.micDiagnostics.deviceId}
                    onChange={(event) => liveMode.selectMicrophone(event.target.value)}
                    className="max-w-48 rounded border border-surface-3 bg-surface-2 px-1 py-0.5 text-[9px] text-zinc-400"
                  >
                    {liveMode.micDiagnostics.availableInputs.map((input) => (
                      <option key={input.deviceId} value={input.deviceId}>{input.label}</option>
                    ))}
                  </select>
                  <label className="flex items-center gap-1 whitespace-nowrap text-[9px] text-zinc-500">
                    Gain {liveMode.micGain.toFixed(1)}×
                    <input
                      aria-label="Microphone gain"
                      type="range"
                      min="0.25"
                      max="4"
                      step="0.05"
                      value={liveMode.micGain}
                      onChange={(event) => liveMode.setMicGain(Number(event.target.value))}
                      className="h-1 w-20 accent-label-500"
                    />
                  </label>
                  <span
                    data-testid="mic-diagnostics"
                    className="whitespace-nowrap text-[9px] text-zinc-600"
                    title={`${liveMode.micDiagnostics.deviceLabel}; ${liveMode.micDiagnostics.frameCount} audio frames`}
                  >
                    {liveMode.micDiagnostics.trackMuted ? "muted" : `${Math.max(-96, Math.round(20 * Math.log10(Math.max(liveMode.micDiagnostics.rawRms, 0.000016))))} dB`} · neural {Math.round(liveMode.micDiagnostics.neuralSpeech * 100)}%
                  </span>
                </div>
              )}
            </>
          )}
        </div>
      )}

      <div className="mb-2 flex shrink-0 items-center gap-2">
        <input
          type="text"
          value={replyText}
          onChange={(e) => setReplyText(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") void handleSendReply(); }}
          placeholder="Reply to the table…"
          className="flex-1 rounded-lg border border-surface-3 bg-surface-2 px-2.5 py-1.5 text-xs text-zinc-200 placeholder-zinc-600 focus:border-zinc-500 focus:outline-none"
        />
        <button
          type="button"
          onClick={() => { void handleSendReply(); }}
          disabled={replySending || !replyText.trim()}
          className="btn-primary px-3 py-1.5 text-xs disabled:opacity-40"
        >
          Send
        </button>
      </div>
      {replyError && <p className="mb-2 shrink-0 text-[10px] text-red-400">{replyError}</p>}
      {latestArtistMessage && (
        <div className="mb-2 shrink-0 rounded-lg border border-label-500/20 bg-label-500/5 px-2.5 py-1.5">
          <p className="text-[9px] font-semibold uppercase tracking-wide text-label-400">You</p>
          <p className="mt-0.5 line-clamp-2 text-[11px] text-zinc-300">
            {latestArtistMessage.message}
          </p>
        </div>
      )}

      <div className="flex min-h-0 flex-1 gap-3 overflow-hidden">
        <div
          className={`flex min-h-0 items-center justify-center overflow-hidden ${
            showArtwork ? "flex-[3]" : "flex-1"
          }`}
        >
          <div style={{ height: "100%", aspectRatio: "1", maxWidth: "100%" }}>
            <RoundTableCanvas
              track={track}
              outboundByAgent={outboundByAgent}
              outboundStream={outboundStream}
              phaseInfo={phaseInfo}
              summary={summary}
              verdict={verdict}
              onAct={() => { void act(); }}
              acting={acting}
            />
          </div>
        </div>
        {showArtwork && (
          <div className="flex min-h-0 flex-[2] flex-col overflow-hidden">
            <ArtworkVariants trackId={track.id} trackTitle={track.title ?? "Untitled"} />
          </div>
        )}
      </div>
    </div>
  );
}
