import { useState } from "react";
import { useAgentMessages } from "../hooks/useAgentMessages";
import type { AgentName } from "../hooks/useAgentMessages";
import type { Feedback } from "../lib/hermes-bridge";
import MessageBubble from "./MessageBubble";
import { sendAgentMessage } from "../lib/hermes-bridge";

const AGENT_COLORS: Record<AgentName, string> = {
  intake: "border-l-zinc-500",
  a_and_r: "border-l-emerald-500",
  kallman: "border-l-amber-500",
  manager: "border-l-blue-500",
  creative_director: "border-l-purple-500",
  janick: "border-l-cyan-500",
  rhone: "border-l-rose-500",
  rubin: "border-l-lime-500",
  bandcamp: "border-l-orange-500",
  system: "border-l-zinc-500",
};

const AGENT_LABELS: Record<AgentName, string> = {
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
  trackId: number | null;
  messages?: Feedback[];
  expanded?: boolean;
  onClose?: () => void;
  title?: string;
}

export default function AgentThread({
  agent,
  trackId,
  messages: providedMessages,
  expanded = false,
  onClose,
  title,
}: Props) {
  const { messages: loadedMessages, refresh } = useAgentMessages(trackId);
  const messages = providedMessages ?? loadedMessages;
  const agentMessages = messages.filter((m) => m.agent === agent);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);

  const handleSend = async () => {
    if (!input.trim() || sending) return;
    setSending(true);
    try {
      await sendAgentMessage(agent, input.trim(), trackId);
      setInput("");
      await refresh();
    } catch (err) {
      console.error("Failed to send:", err);
    } finally {
      setSending(false);
    }
  };

  if (!expanded) {
    const lastMessage = agentMessages[agentMessages.length - 1];
    return (
      <div className={`card border-l-4 ${AGENT_COLORS[agent]}`}>
        <p className="text-xs font-semibold text-zinc-400 mb-1">{AGENT_LABELS[agent]}</p>
        <p className="text-sm text-zinc-300 truncate">
          {lastMessage?.message ?? "No messages yet"}
        </p>
      </div>
    );
  }

  return (
    <div className={`card border-l-4 ${AGENT_COLORS[agent]} flex flex-col h-full`}>
      <div className="mb-3 flex items-start justify-between gap-3 border-b border-surface-3 pb-3">
        <div className="min-w-0">
          <p className="text-sm font-semibold text-zinc-300">
            {AGENT_LABELS[agent]}
          </p>
          {title && (
            <p className="mt-1 text-xs text-zinc-500">
              {title}
            </p>
          )}
        </div>
        {onClose && (
          <button
            type="button"
            onClick={onClose}
            className="btn-ghost px-2 py-1 text-xs"
            title="Close"
          >
            Close
          </button>
        )}
      </div>
      <div className="flex-1 overflow-y-auto space-y-1 mb-3">
        {agentMessages.length === 0 ? (
          <p className="text-sm text-zinc-600 text-center py-8">No messages yet</p>
        ) : (
          agentMessages.map((msg) => (
            <MessageBubble
              key={msg.id}
              agent={agent}
              message={msg.message}
              timestamp={msg.created_at}
              direction={msg.direction as "inbound" | "outbound"}
            />
          ))
        )}
      </div>
      <div className="flex gap-2">
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleSend()}
          placeholder="Reply..."
          className="flex-1 bg-surface-2 border border-surface-3 rounded-lg px-3 py-2 text-sm text-zinc-200 placeholder-zinc-600 focus:outline-none focus:border-zinc-500"
        />
        <button
          onClick={handleSend}
          disabled={sending || !input.trim()}
          className="btn-primary text-sm disabled:opacity-40"
        >
          Send
        </button>
      </div>
    </div>
  );
}
