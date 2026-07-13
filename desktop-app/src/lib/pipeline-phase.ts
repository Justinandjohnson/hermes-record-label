import type { ReleaseState } from "./state-machine";
import type { Feedback } from "./hermes-bridge";
import type { AgentName } from "../hooks/useAgentMessages";

// Agents expected to post during the initial review round
export const REVIEW_AGENTS: AgentName[] = [
  "a_and_r",
  "kallman",
  "janick",
  "rhone",
  "rubin",
  "manager",
];

export type PipelinePhase =
  | "draft"           // DRAFT — waiting for first drop
  | "analyzing"       // IN_REVIEW + ack — Gemini reading audio
  | "feedback-ready"  // FEEDBACK_GIVEN — all (or most) agents have posted
  | "pending-agents"  // FEEDBACK_GIVEN — some agents still in conductor queue
  | "approved"        // APPROVED through ART_APPROVED
  | "release"         // RELEASE_READY through UPLOADING
  | "live";           // RELEASED

export interface PhaseInfo {
  phase: PipelinePhase;
  isAnalyzing: boolean;
  isPendingAgents: boolean;
  agentsWhoPosted: Set<AgentName>;
  agentsPending: AgentName[];
  hasAck: boolean;
  hasAnalysis: boolean;
}

export function derivePipelinePhase(
  state: ReleaseState,
  messages: Feedback[],
): PhaseInfo {
  const hasAck = messages.some((m) => m.intent === "new_track_ack");
  const hasAnalysis = messages.some((m) => m.intent === "analysis_feedback");

  const agentsWhoPosted = new Set<AgentName>(
    messages
      .filter(
        (m) =>
          m.direction === "outbound" &&
          REVIEW_AGENTS.includes(m.agent as AgentName),
      )
      .map((m) => m.agent as AgentName),
  );
  const agentsPending = REVIEW_AGENTS.filter((a) => !agentsWhoPosted.has(a));

  let phase: PipelinePhase;

  if (state === "DRAFT") {
    phase = "draft";
  } else if (state === "IN_REVIEW") {
    // Once the ack appears (transition happens first) we're in the analysis gap.
    // Before the ack appears it's a very brief transient — treat same as analyzing.
    phase = "analyzing";
  } else if (state === "FEEDBACK_GIVEN") {
    // Conductor may be holding some messages — show pending if any REVIEW_AGENTS
    // haven't posted yet.
    phase = agentsPending.length > 0 ? "pending-agents" : "feedback-ready";
  } else if (
    state === "APPROVED" ||
    state === "ART_NEEDED" ||
    state === "ART_SUBMITTED" ||
    state === "ART_APPROVED"
  ) {
    phase = "approved";
  } else if (
    state === "RELEASE_READY" ||
    state === "PREFLIGHT" ||
    state === "UPLOADING"
  ) {
    phase = "release";
  } else if (state === "RELEASED") {
    phase = "live";
  } else {
    phase = "feedback-ready";
  }

  return {
    phase,
    isAnalyzing: phase === "analyzing",
    isPendingAgents: phase === "pending-agents",
    agentsWhoPosted,
    agentsPending,
    hasAck,
    hasAnalysis,
  };
}
