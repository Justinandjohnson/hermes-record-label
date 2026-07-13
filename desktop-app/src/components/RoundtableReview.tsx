import { useMemo, useState } from "react";
import { isAgentName } from "../hooks/useAgentMessages";
import type { AgentName } from "../hooks/useAgentMessages";
import { useLiveMode } from "../hooks/useLiveMode";
import type { LiveModeMicState } from "../hooks/useLiveMode";
import { useSegments } from "../hooks/useSegments";
import { useVerdict } from "../hooks/useVerdict";
import { sendAgentMessage } from "../lib/hermes-bridge";
import type { Feedback, Track } from "../lib/hermes-bridge";
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
}

export default function RoundtableReview({ track, messages, title = "Roundtable" }: Props) {
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

  const phaseInfo = track ? derivePipelinePhase(track.state as ReleaseState, messages) : null;

  const { verdict, acting, act } = useVerdict(track?.id ?? null);
  const { segments } = useSegments(track?.id ?? null);
  const liveMode = useLiveMode({ trackId: track?.id ?? null, outboundStream, phaseInfo });

  const [replyText, setReplyText] = useState("");
  const [replySending, setReplySending] = useState(false);
  const [replyError, setReplyError] = useState<string | null>(null);

  const handleSendReply = async () => {
    if (!track || !replyText.trim() || replySending) return;
    setReplySending(true);
    setReplyError(null);
    try {
      await sendAgentMessage("a_and_r", replyText.trim(), track.id);
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

      <div className="flex min-h-0 flex-1 items-center justify-center overflow-hidden">
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

      {ART_STATES.has(track.state) && (
        <div className="mt-3 shrink-0">
          <ArtworkVariants
            trackId={track.id}
            trackTitle={track.title ?? "Untitled"}
          />
        </div>
      )}
    </div>
  );
}
