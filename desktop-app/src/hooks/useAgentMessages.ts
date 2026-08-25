import { useState, useEffect, useCallback } from "react";
import { getFeedback } from "../lib/hermes-bridge";
import type { Feedback } from "../lib/hermes-bridge";

export type AgentName =
  | "intake"
  | "a_and_r"
  | "kallman"
  | "manager"
  | "creative_director"
  | "janick"
  | "rhone"
  | "rubin"
  | "bandcamp"
  | "system";

export const AGENT_KEYS: AgentName[] = [
  "intake",
  "a_and_r",
  "kallman",
  "manager",
  "creative_director",
  "janick",
  "rhone",
  "rubin",
  "bandcamp",
  "system",
];

export function isAgentName(agent: string): agent is AgentName {
  return AGENT_KEYS.includes(agent as AgentName);
}

export const AGENT_DISPLAY: Record<AgentName, { name: string; color: string }> = {
  intake: { name: "Intake", color: "text-zinc-300" },
  a_and_r: { name: "A&R", color: "text-agent-ar" },
  kallman: { name: "Kallman", color: "text-amber-300" },
  manager: { name: "Manager", color: "text-agent-manager" },
  creative_director: { name: "Creative Dir", color: "text-agent-creative" },
  janick: { name: "Janick", color: "text-cyan-300" },
  rhone: { name: "Rhone", color: "text-rose-300" },
  rubin: { name: "Rubin", color: "text-lime-300" },
  bandcamp: { name: "Bandcamp", color: "text-agent-bandcamp" },
  system: { name: "System", color: "text-zinc-400" },
};

export function useAgentMessages(trackId: number | null) {
  const [messages, setMessages] = useState<Feedback[]>([]);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    if (trackId === null) {
      setLoading(false);
      return;
    }
    try {
      setMessages(await getFeedback(trackId));
    } catch (err) {
      console.error("Failed to fetch messages:", err);
    } finally {
      setLoading(false);
    }
  }, [trackId]);

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 1500);
    return () => clearInterval(interval);
  }, [refresh]);

  return { messages, loading, refresh };
}
