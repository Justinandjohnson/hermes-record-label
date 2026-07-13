import { isAgentName, useAgentMessages } from "../hooks/useAgentMessages";
import type { AgentName } from "../hooks/useAgentMessages";
import type { Feedback, Track } from "../lib/hermes-bridge";
import type { ReleaseState } from "../lib/state-machine";
import { STATE_LABELS } from "../lib/state-machine";
import { derivePipelinePhase, REVIEW_AGENTS } from "../lib/pipeline-phase";

const AGENTS: {
  key: AgentName;
  name: string;
  short: string;
  border: string;
  bg: string;
  text: string;
}[] = [
  {
    key: "intake",
    name: "Intake",
    short: "I",
    border: "border-zinc-500",
    bg: "bg-zinc-500/10",
    text: "text-zinc-300",
  },
  {
    key: "a_and_r",
    name: "Ravi",
    short: "R",
    border: "border-agent-ar",
    bg: "bg-agent-ar/15",
    text: "text-agent-ar",
  },
  {
    key: "kallman",
    name: "Kallman",
    short: "K",
    border: "border-amber-500",
    bg: "bg-amber-500/10",
    text: "text-amber-300",
  },
  {
    key: "manager",
    name: "Dez",
    short: "D",
    border: "border-agent-manager",
    bg: "bg-agent-manager/15",
    text: "text-agent-manager",
  },
  {
    key: "creative_director",
    name: "Maren",
    short: "M",
    border: "border-agent-creative",
    bg: "bg-agent-creative/15",
    text: "text-agent-creative",
  },
  {
    key: "janick",
    name: "Janick",
    short: "J",
    border: "border-cyan-500",
    bg: "bg-cyan-500/10",
    text: "text-cyan-300",
  },
  {
    key: "rhone",
    name: "Rhone",
    short: "Rh",
    border: "border-rose-500",
    bg: "bg-rose-500/10",
    text: "text-rose-300",
  },
  {
    key: "rubin",
    name: "Rubin",
    short: "Ru",
    border: "border-lime-500",
    bg: "bg-lime-500/10",
    text: "text-lime-300",
  },
  {
    key: "bandcamp",
    name: "Sable",
    short: "S",
    border: "border-agent-bandcamp",
    bg: "bg-agent-bandcamp/15",
    text: "text-agent-bandcamp",
  },
  {
    key: "system",
    name: "System",
    short: "S",
    border: "border-zinc-500",
    bg: "bg-zinc-500/10",
    text: "text-zinc-400",
  },
];

const WORKFLOW_EVENT_META: Record<
  string,
  { label: string; detail: string; agent: AgentName }
> = {
  intake_complete: {
    label: "Intake logged",
    detail: "Track registered into the workflow.",
    agent: "intake",
  },
  manager_intake_complete: {
    label: "Manager opened review",
    detail: "Project context and review pass were opened.",
    agent: "manager",
  },
  new_track_ack: {
    label: "Ravi acknowledged drop",
    detail: "A&R pulled the track into review.",
    agent: "a_and_r",
  },
  analysis_feedback: {
    label: "A&R analysis complete",
    detail: "Ravi finished the first-pass music review.",
    agent: "a_and_r",
  },
  early_conviction_feedback: {
    label: "Kallman gut-check complete",
    detail: "First-instinct pass landed before wider review.",
    agent: "kallman",
  },
  panel_session_started: {
    label: "Manager checked panel routing",
    detail: "Human QC branch was evaluated.",
    agent: "manager",
  },
  stems_ready: {
    label: "System split stems",
    detail: "Stem separation completed for downstream review.",
    agent: "system",
  },
  vision_assessment: {
    label: "Janick reviewed vision",
    detail: "Vision/world-building read is complete.",
    agent: "janick",
  },
  cultural_authenticity_read: {
    label: "Rhone reviewed culture",
    detail: "Cultural/authenticity read is complete.",
    agent: "rhone",
  },
  essential_question_review: {
    label: "Rubin reviewed essence",
    detail: "Core-song/essence read is complete.",
    agent: "rubin",
  },
  review_round_summary: {
    label: "Manager set the gate",
    detail: "Consensus summary written; waiting for artist decision.",
    agent: "manager",
  },
  track_approved_notification: {
    label: "Manager advanced approval",
    detail: "Track moved into release workflow.",
    agent: "manager",
  },
  artwork_needed: {
    label: "Creative gate opened",
    detail: "Artwork/visual direction is now required.",
    agent: "creative_director",
  },
  studio_queue_delivery: {
    label: "Studio delivered a queued message",
    detail: "Conductor approved and surfaced an agent message.",
    agent: "system",
  },
};

const WAITING_BY_STATE: Record<
  ReleaseState,
  { label: string; detail: string; owner: AgentName | "artist" | "none" }
> = {
  DRAFT: {
    label: "Waiting for review",
    detail: "Move the track into review when the demo is ready.",
    owner: "artist",
  },
  IN_REVIEW: {
    label: "Waiting for Ravi",
    detail: "A&R feedback is next.",
    owner: "a_and_r",
  },
  FEEDBACK_GIVEN: {
    label: "Waiting for you",
    detail: "Approve the track or upload a revision.",
    owner: "artist",
  },
  APPROVED: {
    label: "Waiting for Maren",
    detail: "Creative direction and artwork are next.",
    owner: "creative_director",
  },
  ART_NEEDED: {
    label: "Waiting for artwork",
    detail: "Submit cover art for review.",
    owner: "artist",
  },
  ART_SUBMITTED: {
    label: "Waiting for Maren",
    detail: "Artwork review is next.",
    owner: "creative_director",
  },
  ART_APPROVED: {
    label: "Waiting for Dez",
    detail: "Release date and rollout timing are next.",
    owner: "manager",
  },
  RELEASE_READY: {
    label: "Waiting for Sable",
    detail: "Bandcamp preflight is next.",
    owner: "bandcamp",
  },
  PREFLIGHT: {
    label: "Waiting for Sable",
    detail: "Metadata and package validation are running.",
    owner: "bandcamp",
  },
  UPLOADING: {
    label: "Waiting for Sable",
    detail: "Bandcamp upload confirmation is next.",
    owner: "bandcamp",
  },
  RELEASED: {
    label: "Released",
    detail: "No active wait.",
    owner: "none",
  },
};

interface Props {
  track: Track;
  messages?: Feedback[];
  variant?: "compact" | "full";
}

function Waveform({ color = "bg-emerald-400" }: { color?: string }) {
  return (
    <span className="flex items-end gap-0.5 h-3">
      {[0, 1, 2, 3, 4].map((i) => (
        <span key={i} className={`waveform-bar w-0.5 h-full ${color}`} />
      ))}
    </span>
  );
}

function TypingDots({ color = "bg-zinc-400" }: { color?: string }) {
  return (
    <span className="flex items-center gap-0.5">
      {[0, 1, 2].map((i) => (
        <span key={i} className={`typing-dot h-1 w-1 ${color}`} />
      ))}
    </span>
  );
}

function formatRelativeTime(dateStr: string): string {
  const date = new Date(dateStr);
  const diff = Date.now() - date.getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

function latestAgentActions(messages: Feedback[]) {
  return messages.reduce<Partial<Record<AgentName, Feedback>>>((acc, message) => {
    if (message.direction === "outbound" && isAgentName(message.agent)) {
      acc[message.agent] = message;
    }
    return acc;
  }, {});
}

function workflowTimeline(messages: Feedback[]) {
  return messages
    .filter((message) => message.direction === "outbound" && message.intent)
    .map((message) => {
      const meta = WORKFLOW_EVENT_META[message.intent ?? ""];
      if (!meta) return null;
      return {
        id: message.id,
        created_at: message.created_at,
        label: meta.label,
        detail: meta.detail,
        agent: meta.agent,
        message: message.message,
      };
    })
    .filter((step): step is NonNullable<typeof step> => Boolean(step));
}

export default function PostDropFlowStatus({
  track,
  messages: providedMessages,
  variant = "compact",
}: Props) {
  const { messages: loadedMessages, loading } = useAgentMessages(
    providedMessages !== undefined ? null : track.id,
  );
  const messages = providedMessages ?? loadedMessages;
  const state = track.state as ReleaseState;
  const phaseInfo = derivePipelinePhase(state, messages);
  const waiting = WAITING_BY_STATE[state] ?? {
    label: `Waiting at ${track.state}`,
    detail: "No release flow mapping found for this state.",
    owner: "none" as const,
  };
  const actions = latestAgentActions(messages);
  const timeline = workflowTimeline(messages);
  const lastAction = Object.values(actions)
    .filter((message): message is Feedback => Boolean(message))
    .sort(
      (a, b) =>
        new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
    )[0];
  const lastAgentName =
    lastAction && AGENTS.find((agent) => agent.key === lastAction.agent)?.name;

  if (variant === "full") {
    return (
      <div className="space-y-3">
        {/* Live activity panel — visible while audio is being analyzed or agents are still writing */}
        {(phaseInfo.isAnalyzing || phaseInfo.isPendingAgents) && (
          <div
            className={`rounded-lg border px-3 py-2.5 transition-all ${
              phaseInfo.isAnalyzing
                ? "scan-shimmer border-emerald-500/40 bg-emerald-500/5"
                : "border-blue-500/30 bg-blue-500/5"
            }`}
          >
            {phaseInfo.isAnalyzing ? (
              <div className="flex items-center gap-2">
                <Waveform color="bg-emerald-400" />
                <p className="text-xs font-semibold text-emerald-300">
                  Gemini is analyzing the audio
                </p>
                <span className="ml-auto flex items-center gap-1">
                  <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 live-dot" />
                  <span className="text-[10px] text-emerald-500">Live</span>
                </span>
              </div>
            ) : (
              <div>
                <div className="flex items-center gap-2 mb-1.5">
                  <TypingDots color="bg-blue-400" />
                  <p className="text-xs font-semibold text-blue-300">
                    {phaseInfo.agentsPending.length} agent{phaseInfo.agentsPending.length === 1 ? "" : "s"} still writing
                  </p>
                </div>
                <div className="flex flex-wrap gap-1">
                  {phaseInfo.agentsPending.map((agentKey) => {
                    const agent = AGENTS.find((a) => a.key === agentKey);
                    return (
                      <span
                        key={agentKey}
                        className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] ${
                          agent
                            ? `${agent.border} ${agent.bg} ${agent.text}`
                            : "border-surface-3 text-zinc-500"
                        }`}
                      >
                        {agent?.name ?? agentKey}
                        <TypingDots color={agent ? "bg-current" : "bg-zinc-500"} />
                      </span>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        )}

        <div>
          <p className="text-[10px] text-zinc-600 uppercase tracking-wide mb-1">
            Waiting next
          </p>
          <div className="bg-surface-2 border border-surface-3 rounded-lg px-3 py-2">
            <p className="text-sm font-semibold text-zinc-200">
              {waiting.label}
            </p>
            <p className="text-xs text-zinc-500 mt-0.5">{waiting.detail}</p>
          </div>
        </div>

        <div>
          <p className="text-[10px] text-zinc-600 uppercase tracking-wide mb-2">
            Workflow timeline
          </p>
          <div className="space-y-2">
            {timeline.length === 0 ? (
              <div className="rounded-lg border border-surface-3 bg-surface-2/50 px-3 py-2">
                <p className="text-xs text-zinc-500">No completed workflow steps yet.</p>
              </div>
            ) : (
              timeline.map((step, index) => {
                const agent = AGENTS.find((item) => item.key === step.agent);
                return (
                  <div key={step.id} className="flex gap-3 slide-in">
                    <div className="flex flex-col items-center pt-0.5">
                      <span
                        className={`h-6 w-6 rounded-full border text-[10px] font-bold flex items-center justify-center ${
                          agent ? `${agent.border} ${agent.bg} ${agent.text}` : "border-surface-3 text-zinc-500"
                        }`}
                      >
                        {agent?.short ?? "•"}
                      </span>
                      {index < timeline.length - 1 && (
                        <span className="mt-1 h-6 w-px bg-surface-3" />
                      )}
                    </div>
                    <div className="min-w-0 flex-1 pb-2">
                      <div className="flex items-center justify-between gap-3">
                        <p className="text-xs font-semibold text-zinc-200">{step.label}</p>
                        <span className="text-[10px] text-zinc-600 whitespace-nowrap">
                          {formatRelativeTime(step.created_at)}
                        </span>
                      </div>
                      <p className="text-[10px] text-zinc-500 mt-0.5">{step.detail}</p>
                      <p className="text-[11px] text-zinc-400 mt-1">{step.message}</p>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>

        <div>
          <p className="text-[10px] text-zinc-600 uppercase tracking-wide mb-2">
            Agent actions
          </p>
          <div className="grid grid-cols-2 gap-2">
            {AGENTS.map((agent) => {
              const action = actions[agent.key];
              const isWaiting = waiting.owner === agent.key;
              const isPendingReviewAgent =
                phaseInfo.isPendingAgents &&
                REVIEW_AGENTS.includes(agent.key as typeof REVIEW_AGENTS[number]) &&
                phaseInfo.agentsPending.includes(agent.key as typeof REVIEW_AGENTS[number]);
              return (
                <div
                  key={agent.key}
                  className={`border rounded-lg px-2.5 py-2 ${
                    action
                      ? `${agent.border} ${agent.bg}`
                      : isWaiting
                        ? "border-label-500/50 bg-label-500/5"
                        : "border-surface-3 bg-surface-2/50"
                  }`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className={`text-xs font-semibold ${agent.text}`}>
                      {agent.name}
                    </span>
                    {isPendingReviewAgent ? (
                      <TypingDots color="bg-blue-400" />
                    ) : (
                      <span className="text-[10px] text-zinc-500">
                        {action
                          ? "Acted"
                          : isWaiting
                            ? "Waiting"
                            : "Pending"}
                      </span>
                    )}
                  </div>
                  <p className="text-[10px] text-zinc-500 mt-1 truncate">
                    {isPendingReviewAgent
                      ? "Writing…"
                      : action
                        ? formatRelativeTime(action.created_at)
                        : isWaiting
                          ? STATE_LABELS[state] ?? state
                          : "No action yet"}
                  </p>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    );
  }

  // Compact variant
  return (
    <div className="mt-3 border-t border-surface-3 pt-2">
      {phaseInfo.isAnalyzing ? (
        <div className="flex items-center gap-2">
          <Waveform color="bg-emerald-400" />
          <p className="text-[10px] font-semibold text-emerald-300">Analyzing audio</p>
          <span className="ml-auto h-1.5 w-1.5 rounded-full bg-emerald-400 live-dot" />
        </div>
      ) : (
        <div className="flex items-center justify-between gap-3">
          <div className="min-w-0">
            <p className="text-[10px] font-semibold text-zinc-400 truncate">
              {waiting.label}
            </p>
            <p className="text-[10px] text-zinc-600 truncate">
              {waiting.detail}
            </p>
          </div>
          <div className="flex items-center gap-1 shrink-0">
            {AGENTS.map((agent) => {
              const action = actions[agent.key];
              const isWaiting = waiting.owner === agent.key;
              const isPendingReview =
                phaseInfo.isPendingAgents &&
                phaseInfo.agentsPending.includes(agent.key as typeof REVIEW_AGENTS[number]);
              return (
                <span
                  key={agent.key}
                  title={`${agent.name}: ${
                    action
                      ? `acted ${formatRelativeTime(action.created_at)}`
                      : isWaiting
                        ? "waiting now"
                        : "pending"
                  }`}
                  className={`relative h-5 w-5 rounded-full border text-[9px] font-bold flex items-center justify-center ${
                    action
                      ? `${agent.border} ${agent.bg} ${agent.text}`
                      : isWaiting
                        ? "border-label-500 text-label-400 bg-label-500/10"
                        : "border-surface-3 text-zinc-600 bg-surface-2"
                  }`}
                >
                  {isPendingReview ? (
                    <span className="absolute inset-0 flex items-end justify-center pb-0.5">
                      <TypingDots color="bg-blue-400" />
                    </span>
                  ) : (
                    agent.short
                  )}
                </span>
              );
            })}
          </div>
        </div>
      )}
      {!phaseInfo.isAnalyzing && lastAction && (
        <p className="text-[10px] text-zinc-600 mt-1 truncate">
          Last: {lastAgentName ?? lastAction.agent} · {formatRelativeTime(lastAction.created_at)}
        </p>
      )}
      {!phaseInfo.isAnalyzing && timeline.length > 0 && (
        <p className="text-[10px] text-zinc-500 mt-1 truncate">
          Done: {timeline.map((step) => step.label).slice(-3).join(" · ")}
        </p>
      )}
      {loading && messages.length === 0 && (
        <p className="text-[10px] text-zinc-700 mt-1">Syncing agent actions...</p>
      )}
    </div>
  );
}
