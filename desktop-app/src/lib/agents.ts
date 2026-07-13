import type { AgentName } from "../hooks/useAgentMessages";

export const AGENT_ORDER: AgentName[] = [
  "manager",
  "a_and_r",
  "kallman",
  "janick",
  "rhone",
  "rubin",
  "creative_director",
  "bandcamp",
  "intake",
  "system",
];

export const AGENT_META: Record<
  AgentName,
  {
    label: string;
    role: string;
    initial: string;
    border: string;
    badge: string;
    dot: string;
    glow: string;
    svgColor: string;
    ringColor: string;
  }
> = {
  manager: {
    label: "Dez",
    role: "Conductor",
    initial: "D",
    border: "border-blue-500",
    badge: "bg-blue-500/10 text-blue-300",
    dot: "bg-blue-400",
    glow: "shadow-blue-500/15",
    svgColor: "#3b82f6",
    ringColor: "rgba(59,130,246,0.55)",
  },
  a_and_r: {
    label: "Ravi",
    role: "A&R",
    initial: "R",
    border: "border-emerald-500",
    badge: "bg-emerald-500/10 text-emerald-300",
    dot: "bg-emerald-400",
    glow: "shadow-emerald-500/15",
    svgColor: "#10b981",
    ringColor: "rgba(16,185,129,0.55)",
  },
  kallman: {
    label: "Kallman",
    role: "First instinct",
    initial: "K",
    border: "border-amber-500",
    badge: "bg-amber-500/10 text-amber-300",
    dot: "bg-amber-400",
    glow: "shadow-amber-500/15",
    svgColor: "#f59e0b",
    ringColor: "rgba(245,158,11,0.55)",
  },
  janick: {
    label: "Janick",
    role: "Vision",
    initial: "J",
    border: "border-cyan-500",
    badge: "bg-cyan-500/10 text-cyan-300",
    dot: "bg-cyan-400",
    glow: "shadow-cyan-500/15",
    svgColor: "#06b6d4",
    ringColor: "rgba(6,182,212,0.55)",
  },
  rhone: {
    label: "Rhone",
    role: "Culture",
    initial: "Rh",
    border: "border-rose-500",
    badge: "bg-rose-500/10 text-rose-300",
    dot: "bg-rose-400",
    glow: "shadow-rose-500/15",
    svgColor: "#f43f5e",
    ringColor: "rgba(244,63,94,0.55)",
  },
  rubin: {
    label: "Rubin",
    role: "Essence",
    initial: "Ru",
    border: "border-lime-500",
    badge: "bg-lime-500/10 text-lime-300",
    dot: "bg-lime-400",
    glow: "shadow-lime-500/15",
    svgColor: "#84cc16",
    ringColor: "rgba(132,204,22,0.55)",
  },
  creative_director: {
    label: "Maren",
    role: "Creative",
    initial: "M",
    border: "border-purple-500",
    badge: "bg-purple-500/10 text-purple-300",
    dot: "bg-purple-400",
    glow: "shadow-purple-500/15",
    svgColor: "#a855f7",
    ringColor: "rgba(168,85,247,0.55)",
  },
  bandcamp: {
    label: "Sable",
    role: "Release",
    initial: "S",
    border: "border-orange-500",
    badge: "bg-orange-500/10 text-orange-300",
    dot: "bg-orange-400",
    glow: "shadow-orange-500/15",
    svgColor: "#f97316",
    ringColor: "rgba(249,115,22,0.55)",
  },
  intake: {
    label: "Intake",
    role: "Ingest",
    initial: "I",
    border: "border-zinc-500",
    badge: "bg-zinc-500/10 text-zinc-300",
    dot: "bg-zinc-400",
    glow: "shadow-zinc-500/10",
    svgColor: "#71717a",
    ringColor: "rgba(113,113,122,0.5)",
  },
  system: {
    label: "System",
    role: "Automation",
    initial: "Sys",
    border: "border-zinc-600",
    badge: "bg-zinc-500/10 text-zinc-400",
    dot: "bg-zinc-500",
    glow: "shadow-zinc-500/10",
    svgColor: "#52525b",
    ringColor: "rgba(82,82,91,0.5)",
  },
};

export const SUMMARY_INTENTS = new Set([
  "review_round_summary",
  "track_approved_notification",
  "artwork_needed",
  "analysis_feedback",
]);

export const HIDDEN_INTENTS = new Set([
  "studio_queue_delivery",
]);
