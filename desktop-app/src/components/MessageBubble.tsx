import type { AgentName } from "../hooks/useAgentMessages";

const BUBBLE_COLORS: Record<AgentName, string> = {
  intake: "bg-zinc-900/40 border-zinc-700/30",
  a_and_r: "bg-emerald-900/40 border-emerald-700/30",
  kallman: "bg-amber-900/30 border-amber-700/30",
  manager: "bg-blue-900/40 border-blue-700/30",
  creative_director: "bg-purple-900/40 border-purple-700/30",
  janick: "bg-cyan-900/30 border-cyan-700/30",
  rhone: "bg-rose-900/30 border-rose-700/30",
  rubin: "bg-lime-900/30 border-lime-700/30",
  bandcamp: "bg-orange-900/40 border-orange-700/30",
  system: "bg-zinc-900/50 border-zinc-700/30",
};

const AGENT_NAMES: Record<AgentName, string> = {
  intake: "Intake",
  a_and_r: "Ravi (A&R)",
  kallman: "Kallman",
  manager: "Dez (Manager)",
  creative_director: "Maren (Creative Dir)",
  janick: "Janick",
  rhone: "Rhone",
  rubin: "Rubin",
  bandcamp: "Sable (Bandcamp)",
  system: "System",
};

interface Props {
  agent: AgentName;
  message: string;
  timestamp: string;
  direction: "inbound" | "outbound";
}

export default function MessageBubble({ agent, message, timestamp, direction }: Props) {
  const isArtistReply = direction === "inbound";
  const time = new Date(timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

  if (isArtistReply) {
    return (
      <div className="flex justify-end mb-3">
        <div className="max-w-[75%] bg-surface-2 border border-surface-3 rounded-2xl rounded-br-sm px-4 py-2.5">
          <p className="text-sm text-zinc-200 whitespace-pre-wrap">{message}</p>
          <p className="text-[10px] text-zinc-500 mt-1 text-right">{time}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex justify-start mb-3">
      <div className={`max-w-[75%] border rounded-2xl rounded-bl-sm px-4 py-2.5 ${BUBBLE_COLORS[agent]}`}>
        <p className="text-[11px] font-semibold text-zinc-400 mb-1">{AGENT_NAMES[agent]}</p>
        <p className="text-sm text-zinc-200 whitespace-pre-wrap">{message}</p>
        <p className="text-[10px] text-zinc-500 mt-1">{time}</p>
      </div>
    </div>
  );
}
