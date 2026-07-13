import type { Track } from "../lib/hermes-bridge";
import type { Feedback } from "../lib/hermes-bridge";
import type { ReleaseState } from "../lib/state-machine";
import { STATE_LABELS, STATE_COLORS } from "../lib/state-machine";
import { formatFileSize } from "../lib/audio-formats";
import PostDropFlowStatus from "./PostDropFlowStatus";
import TrackPlaybackButton from "./TrackPlaybackButton";

const AGENT_NOTE_ORDER = [
  "a_and_r",
  "manager",
  "creative_director",
  "bandcamp",
  "intake",
  "kallman",
  "janick",
  "rhone",
  "rubin",
  "system",
] as const;

const AGENT_NOTE_META: Record<
  string,
  { label: string; initial: string; badge: string; border: string }
> = {
  intake: {
    label: "Intake",
    initial: "I",
    badge: "bg-zinc-500/10 text-zinc-300",
    border: "border-zinc-500",
  },
  a_and_r: {
    label: "Ravi",
    initial: "R",
    badge: "bg-emerald-500/10 text-emerald-300",
    border: "border-emerald-500",
  },
  kallman: {
    label: "Kallman",
    initial: "K",
    badge: "bg-amber-500/10 text-amber-300",
    border: "border-amber-500",
  },
  manager: {
    label: "Dez",
    initial: "D",
    badge: "bg-blue-500/10 text-blue-300",
    border: "border-blue-500",
  },
  creative_director: {
    label: "Maren",
    initial: "M",
    badge: "bg-purple-500/10 text-purple-300",
    border: "border-purple-500",
  },
  janick: {
    label: "Janick",
    initial: "J",
    badge: "bg-cyan-500/10 text-cyan-300",
    border: "border-cyan-500",
  },
  rhone: {
    label: "Rhone",
    initial: "Rh",
    badge: "bg-rose-500/10 text-rose-300",
    border: "border-rose-500",
  },
  rubin: {
    label: "Rubin",
    initial: "Ru",
    badge: "bg-lime-500/10 text-lime-300",
    border: "border-lime-500",
  },
  bandcamp: {
    label: "Sable",
    initial: "S",
    badge: "bg-orange-500/10 text-orange-300",
    border: "border-orange-500",
  },
  system: {
    label: "System",
    initial: "Sys",
    badge: "bg-zinc-500/10 text-zinc-400",
    border: "border-zinc-600",
  },
};

interface Props {
  track: Track;
  messages?: Feedback[];
  onClick?: () => void;
  selected?: boolean;
  onVault?: () => void;
  onDelete?: () => void;
  onApprove?: () => void;
  railCompact?: boolean;
}

function latestSummary(messages: Feedback[] | undefined): Feedback | null {
  if (!messages || messages.length === 0) return null;
  const preferred = messages.filter(
    (message) =>
      message.direction === "outbound" &&
      [
        "review_round_summary",
        "analysis_feedback",
        "track_approved_notification",
        "early_conviction_feedback",
        "vision_assessment_requested",
        "cultural_authenticity_requested",
        "essential_question_requested",
      ].includes(message.intent ?? ""),
  );
  return preferred[preferred.length - 1] ?? messages[messages.length - 1] ?? null;
}

function agentLabel(agent: string): string {
  return AGENT_NOTE_META[agent]?.label ?? agent;
}

function formatIntent(intent: string | null): string {
  if (!intent) return "message";
  return intent.replaceAll("_", " ");
}

function formatTime(value: string): string {
  return new Date(value).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
}

function latestAgentNotes(messages: Feedback[] | undefined): Feedback[] {
  if (!messages || messages.length === 0) return [];
  const latestByAgent = new Map<string, Feedback>();
  for (const message of messages) {
    if (message.direction !== "outbound") continue;
    latestByAgent.set(message.agent, message);
  }
  return AGENT_NOTE_ORDER.map((agent) => latestByAgent.get(agent))
    .filter((message): message is Feedback => message !== undefined);
}

function teamStream(messages: Feedback[] | undefined): Feedback[] {
  if (!messages || messages.length === 0) return [];
  return messages
    .filter((message) => message.direction === "outbound" && AGENT_NOTE_META[message.agent])
    .sort(
      (a, b) =>
        new Date(a.created_at).getTime() - new Date(b.created_at).getTime() ||
        a.id - b.id,
    );
}

function latestHandoff(stream: Feedback[]): string | null {
  if (stream.length < 2) return null;
  const latest = stream[stream.length - 1];
  const previous = [...stream].reverse().find((message) => message.agent !== latest.agent);
  if (!previous) return null;
  return `${agentLabel(previous.agent)} -> ${agentLabel(latest.agent)}`;
}

export default function TrackCard({
  track,
  messages,
  onClick,
  selected = false,
  onVault,
  onDelete,
  onApprove,
  railCompact = false,
}: Props) {
  const state = track.state as ReleaseState;
  const summary = latestSummary(messages);
  const notes = latestAgentNotes(messages);
  const stream = teamStream(messages);
  const recentStream = stream.slice(railCompact ? -2 : -4);
  const handoff = latestHandoff(stream);
  const latestRoomMessage = stream[stream.length - 1] ?? null;

  return (
    <div
      role={onClick ? "button" : undefined}
      tabIndex={onClick ? 0 : undefined}
      className={`card w-full text-left transition-all group ${
        selected
          ? "border-label-500/70 bg-label-500/5"
          : "hover:border-surface-3/80"
      } ${onClick ? "cursor-pointer" : "cursor-default"} ${railCompact ? "p-3" : ""}`}
      onClick={onClick}
      onKeyDown={(event) => {
        if (!onClick) return;
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onClick();
        }
      }}
      aria-pressed={selected}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 min-w-0">
            <h3
              className={`font-semibold text-zinc-100 truncate group-hover:text-label-400 transition-colors min-w-0 ${
                railCompact ? "text-sm" : ""
              }`}
            >
              {track.title || "Untitled Track"}
            </h3>
            <TrackPlaybackButton trackId={track.id} compact />
          </div>
          <div
            className={`mt-1 flex items-center gap-2 text-zinc-500 ${
              railCompact ? "text-[10px]" : "text-xs"
            }`}
          >
            {track.format && <span className="uppercase">{track.format}</span>}
            {!railCompact && track.file_size && <span>{formatFileSize(track.file_size)}</span>}
            {track.duration_seconds && (
              <span>
                {Math.floor(track.duration_seconds / 60)}:{String(Math.floor(track.duration_seconds % 60)).padStart(2, "0")}
              </span>
            )}
            {track.version > 1 && <span className="text-label-500">v{track.version}</span>}
          </div>
        </div>
        <div className="flex flex-col items-end gap-1 shrink-0">
          <span className={`state-badge text-white ${STATE_COLORS[state] ?? "bg-zinc-600"}`}>
            {STATE_LABELS[state] ?? state}
          </span>
          {selected && (
            <span className="text-[10px] font-semibold text-label-400">
              Selected
            </span>
          )}
        </div>
      </div>
      <div className={railCompact ? "mt-2 max-h-20 overflow-hidden" : ""}>
        <PostDropFlowStatus track={track} messages={messages} />
      </div>
      {stream.length > 0 && (
        <div
          className={`rounded-lg border border-surface-3 bg-surface-2/35 ${
            railCompact ? "mt-2 px-2.5 py-2" : "mt-3 px-3 py-2"
          }`}
        >
          <div className="flex items-center justify-between gap-3">
            <div className="min-w-0">
              <p className="text-[10px] font-semibold uppercase tracking-wide text-zinc-500">
                Team room
              </p>
              <p className="mt-1 truncate text-xs text-zinc-400">
                {handoff ?? `${agentLabel(latestRoomMessage.agent)} has the floor`}
              </p>
            </div>
            <div className="flex shrink-0 -space-x-1">
              {notes.slice(0, railCompact ? 5 : 6).map((note) => {
                const meta = AGENT_NOTE_META[note.agent];
                return (
                  <span
                    key={note.agent}
                    className={`flex items-center justify-center rounded-full border px-1.5 font-semibold ${meta.border} ${meta.badge} ${
                      railCompact ? "h-6 min-w-6 text-[9px]" : "h-7 min-w-7 text-[10px]"
                    }`}
                    title={`${meta.label}: ${formatIntent(note.intent)}`}
                  >
                    {meta.initial}
                  </span>
                );
              })}
            </div>
          </div>
          {!selected && latestRoomMessage && (
            <p
              className={`mt-2 text-xs leading-5 text-zinc-500 ${
                railCompact ? "line-clamp-1" : "line-clamp-2"
              }`}
            >
              {latestRoomMessage.message}
            </p>
          )}
        </div>
      )}
      {selected && summary && (
        !railCompact && (
        <div
          className={`rounded-lg border border-surface-3 bg-surface-2/60 ${
            railCompact ? "mt-2 px-2.5 py-2" : "mt-3 px-3 py-2"
          }`}
        >
          <p className="text-[10px] font-semibold uppercase tracking-wide text-zinc-500">
            Pinned call
          </p>
          <p className={`mt-1 text-xs text-zinc-300 ${railCompact ? "line-clamp-2 leading-5" : ""}`}>
            {summary.message}
          </p>
        </div>
        )
      )}
      {selected && recentStream.length > 0 && (
        !railCompact && (
        <div
          className={`rounded-lg border border-surface-3 bg-surface-2/40 ${
            railCompact ? "mt-2 px-2.5 py-2" : "mt-3 px-3 py-3"
          }`}
        >
          <p className="text-[10px] font-semibold uppercase tracking-wide text-zinc-500">
            Shared exchange
          </p>
          <div className="mt-2 space-y-2">
            {recentStream.slice(railCompact ? -2 : -4).map((note, index) => {
              const meta = AGENT_NOTE_META[note.agent];
              const previous = recentStream.slice(railCompact ? -2 : -4)[index - 1];
              const showHandoff = previous && previous.agent !== note.agent;
              return (
                <div key={note.id}>
                  {showHandoff && (
                    <div className="mb-2 ml-3 flex items-center gap-2 text-[10px] text-zinc-600">
                      <span className="h-px w-5 bg-surface-3" />
                      <span>
                        {agentLabel(previous.agent)} handed to {agentLabel(note.agent)}
                      </span>
                    </div>
                  )}
                  <div
                    className={`rounded-md border border-surface-3/80 bg-surface-1/70 px-2.5 py-2 border-l-4 ${meta.border}`}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <div className="flex min-w-0 items-center gap-2">
                        <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${meta.badge}`}>
                          {meta.label}
                        </span>
                        <p className="truncate text-[10px] text-zinc-600">
                          {formatIntent(note.intent)}
                        </p>
                      </div>
                      <p className="shrink-0 text-[10px] text-zinc-600">
                        {formatTime(note.created_at)}
                      </p>
                    </div>
                    <div className={railCompact ? "mt-1" : "mt-1 max-h-24 overflow-y-auto pr-1"}>
                      <p className={`text-xs text-zinc-400 whitespace-pre-wrap ${railCompact ? "line-clamp-1" : ""}`}>
                        {note.message}
                      </p>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
        )
      )}
      {selected && state === "FEEDBACK_GIVEN" && (
        !railCompact && (
        <div
          className={`rounded-lg border border-label-500/20 bg-label-500/5 ${
            railCompact ? "mt-2 px-2.5 py-2" : "mt-3 px-3 py-3"
          }`}
        >
          <p className="text-[10px] font-semibold uppercase tracking-wide text-label-400">
            Next step
          </p>
          <p className="mt-1 text-xs text-zinc-400 line-clamp-1">
            {railCompact
              ? "Approve this version or upload a revision."
              : "The review round is done. Either approve this version or upload a revision."}
          </p>
          <div className={`${railCompact ? "mt-2" : "mt-3"} flex gap-2`}>
            {onApprove && (
              <button
                type="button"
                className="btn-primary text-xs px-3 py-1.5"
                onClick={(event) => {
                  event.stopPropagation();
                  onApprove();
                }}
              >
                Approve track
              </button>
            )}
            <a
              href="/drop"
              className="btn-ghost text-xs px-3 py-1.5"
              onClick={(event) => event.stopPropagation()}
            >
              Upload revision
            </a>
          </div>
        </div>
        )
      )}
      {selected && (onVault || onDelete) && (
        !railCompact && (
        <div
          className={`flex justify-end gap-2 border-t border-surface-3 ${
            railCompact ? "mt-2 pt-2" : "mt-3 pt-3"
          }`}
        >
          {onVault && (
            <button
              type="button"
              className="btn-ghost text-xs px-3 py-1.5"
              onClick={(event) => {
                event.stopPropagation();
                onVault();
              }}
            >
              Vault
            </button>
          )}
          {onDelete && (
            <button
              type="button"
              className="btn-ghost text-xs px-3 py-1.5 text-red-300 hover:text-red-200"
              onClick={(event) => {
                event.stopPropagation();
                onDelete();
              }}
            >
              Delete
            </button>
          )}
        </div>
        )
      )}
    </div>
  );
}
