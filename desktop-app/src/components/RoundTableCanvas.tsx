import { useEffect, useMemo, useRef, useState } from "react";
import type { AgentName } from "../hooks/useAgentMessages";
import { isAgentName } from "../hooks/useAgentMessages";
import type { Feedback, Track } from "../lib/hermes-bridge";
import type { PhaseInfo } from "../lib/pipeline-phase";
import type { Verdict } from "../lib/verdict";
import { NEXT_ACTION_META, VERDICT_META } from "../lib/verdict";
import { AGENT_META, AGENT_ORDER } from "../lib/agents";
import VoicePlayButton from "./VoicePlayButton";

const SEAT_RADIUS = 38;
const TABLE_RADIUS = 21;

function getSeatCoords(index: number, total: number) {
  const angle = (2 * Math.PI / total) * index - Math.PI / 2;
  return {
    left: 50 + SEAT_RADIUS * Math.cos(angle),
    top: 50 + SEAT_RADIUS * Math.sin(angle),
    angle,
  };
}

function getArcPath(
  from: { left: number; top: number },
  to: { left: number; top: number },
): string {
  const midLeft = (from.left + to.left) / 2;
  const midTop = (from.top + to.top) / 2;
  const cpLeft = midLeft + (50 - midLeft) * 0.5;
  const cpTop = midTop + (50 - midTop) * 0.5;
  return `M ${from.left.toFixed(2)} ${from.top.toFixed(2)} Q ${cpLeft.toFixed(2)} ${cpTop.toFixed(2)} ${to.left.toFixed(2)} ${to.top.toFixed(2)}`;
}

function Waveform() {
  return (
    <span className="flex h-5 items-end gap-0.5">
      {[0, 1, 2, 3, 4].map((i) => (
        <span key={i} className="waveform-bar h-full w-0.5 bg-emerald-400" />
      ))}
    </span>
  );
}

function TypingDots() {
  return (
    <span className="flex items-center gap-0.5">
      {[0, 1, 2].map((i) => (
        <span key={i} className="typing-dot h-1 w-1 bg-blue-400" />
      ))}
    </span>
  );
}

interface Props {
  track: Track | null;
  outboundByAgent: Map<AgentName, Feedback[]>;
  outboundStream: Feedback[];
  phaseInfo: PhaseInfo | null;
  summary: Feedback | null;
  verdict: Verdict | null;
  onAct: () => void;
  acting: boolean;
}

export default function RoundTableCanvas({
  track,
  outboundByAgent,
  outboundStream,
  phaseInfo,
  summary,
  verdict,
  onAct,
  acting,
}: Props) {
  const seatCoords = useMemo(
    () => AGENT_ORDER.map((_, i) => getSeatCoords(i, AGENT_ORDER.length)),
    [],
  );

  const [selectedAgent, setSelectedAgent] = useState<AgentName | null>(null);
  const [tableOpen, setTableOpen] = useState(false);

  const latestSpeaker = outboundStream.length > 0
    ? (outboundStream[outboundStream.length - 1].agent as AgentName)
    : null;

  // One-shot handoff particle fires when the speaker changes
  const prevSpeakerRef = useRef<AgentName | null>(null);
  const [particleKey, setParticleKey] = useState(0);
  const [particleAgents, setParticleAgents] = useState<{ from: AgentName; to: AgentName } | null>(null);

  useEffect(() => {
    const prev = prevSpeakerRef.current;
    if (latestSpeaker && prev && latestSpeaker !== prev) {
      setParticleAgents({ from: prev, to: latestSpeaker });
      setParticleKey((k) => k + 1);
    }
    prevSpeakerRef.current = latestSpeaker;
  }, [latestSpeaker]);

  // Full ordered speaker sequence (no cap)
  const replayChain = useMemo(() => {
    const result: AgentName[] = [];
    for (const msg of outboundStream) {
      const agent = msg.agent as AgentName;
      if (result[result.length - 1] !== agent) result.push(agent);
    }
    return result;
  }, [outboundStream]);

  // All unique from→to arcs that occurred (deduplicated for display)
  const arcSet = useMemo(() => {
    const seen = new Set<string>();
    const result: Array<{ from: AgentName; to: AgentName }> = [];
    for (let i = 0; i < replayChain.length - 1; i++) {
      const key = `${replayChain[i]}→${replayChain[i + 1]}`;
      if (!seen.has(key)) {
        seen.add(key);
        result.push({ from: replayChain[i], to: replayChain[i + 1] });
      }
    }
    return result;
  }, [replayChain]);

  // Latest arc key for highlighting
  const latestArcKey = replayChain.length >= 2
    ? `${replayChain[replayChain.length - 2]}→${replayChain[replayChain.length - 1]}`
    : null;

  // Arc for the one-shot transition particle
  const particleArcPath = useMemo(() => {
    if (!particleAgents) return null;
    const fromIdx = AGENT_ORDER.indexOf(particleAgents.from);
    const toIdx = AGENT_ORDER.indexOf(particleAgents.to);
    if (fromIdx === -1 || toIdx === -1) return null;
    return getArcPath(seatCoords[fromIdx], seatCoords[toIdx]);
  }, [particleAgents, seatCoords]);

  // Arc for the active loop particle (the latest transition while live)
  const currentArc = useMemo(() => {
    if (replayChain.length < 2) return null;
    const from = replayChain[replayChain.length - 2];
    const to = replayChain[replayChain.length - 1];
    const fromIdx = AGENT_ORDER.indexOf(from);
    const toIdx = AGENT_ORDER.indexOf(to);
    if (fromIdx === -1 || toIdx === -1) return null;
    return {
      path: getArcPath(seatCoords[fromIdx], seatCoords[toIdx]),
      color: AGENT_META[to]?.svgColor ?? "#71717a",
      toAgent: to,
    };
  }, [replayChain, seatCoords]);

  // Idle detection: 3s after the last message arrives → replay mode
  const [idleMode, setIdleMode] = useState(false);

  useEffect(() => {
    if (outboundStream.length === 0) return;
    setIdleMode(false);
    const t = setTimeout(() => setIdleMode(true), 3000);
    return () => clearTimeout(t);
  }, [outboundStream.length]);

  // Replay step: an ever-incrementing key; derive position from key mod arc count
  const [replayKey, setReplayKey] = useState(0);
  const arcCount = Math.max(1, replayChain.length - 1);
  const replayStep = replayKey % arcCount;

  useEffect(() => {
    if (!idleMode || replayChain.length < 2) return;
    setReplayKey(0);
    // Advance to the next arc every 1.5s — slightly longer than the 1.3s animation
    const interval = setInterval(() => setReplayKey((k) => k + 1), 1500);
    return () => clearInterval(interval);
  }, [idleMode, replayChain.length]);

  // Arc for the current replay step
  const replayArc = useMemo(() => {
    if (!idleMode || replayChain.length < 2) return null;
    const from = replayChain[replayStep];
    const to = replayChain[replayStep + 1];
    if (!from || !to) return null;
    const fromIdx = AGENT_ORDER.indexOf(from);
    const toIdx = AGENT_ORDER.indexOf(to);
    if (fromIdx === -1 || toIdx === -1) return null;
    return {
      path: getArcPath(seatCoords[fromIdx], seatCoords[toIdx]),
      color: AGENT_META[to]?.svgColor ?? "#71717a",
    };
  }, [idleMode, replayStep, replayChain, seatCoords]);

  const latestMessage = outboundStream[outboundStream.length - 1] ?? null;

  // Speech bubble: radius 25 along the agent's angle
  const bubbleCoords = useMemo(() => {
    if (!selectedAgent) return null;
    const idx = AGENT_ORDER.indexOf(selectedAgent);
    const { angle } = seatCoords[idx];
    return {
      left: 50 + 25 * Math.cos(angle),
      top: 50 + 25 * Math.sin(angle),
    };
  }, [selectedAgent, seatCoords]);

  if (!track) return null;

  const dismissAll = () => {
    setSelectedAgent(null);
    setTableOpen(false);
  };

  return (
    <div
      className="relative w-full"
      style={{ aspectRatio: "1" }}
      onClick={dismissAll}
    >
      {/* Table surface */}
      <div
        className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 overflow-hidden rounded-full border border-surface-3 cursor-pointer"
        style={{
          width: `${TABLE_RADIUS * 2}%`,
          height: `${TABLE_RADIUS * 2}%`,
          background:
            "radial-gradient(ellipse at 35% 35%, rgba(255,255,255,0.03) 0%, rgba(0,0,0,0.08) 60%), var(--color-surface-1, #18181b)",
          boxShadow:
            "inset 0 2px 16px rgba(0,0,0,0.45), inset 0 0 0 1px rgba(255,255,255,0.04)",
        }}
        onClick={(e) => {
          e.stopPropagation();
          setSelectedAgent(null);
          setTableOpen((o) => !o);
        }}
      >
        {tableOpen ? (
          <div className="flex h-full flex-col items-center justify-center gap-1 px-2 text-center">
            <span className="rounded-full border border-label-500/20 bg-label-500/10 px-1.5 py-0.5 text-[7px] font-semibold uppercase tracking-wider text-label-400">
              {verdict ? VERDICT_META[verdict.recommendation].label : track.state.replace(/_/g, " ")}
            </span>
            {verdict ? (
              <>
                <p className="text-[8px] font-semibold leading-tight text-zinc-100 line-clamp-2">
                  {verdict.headline}
                </p>
                <p className="text-[7px] leading-tight text-zinc-400 line-clamp-3 whitespace-pre-wrap">
                  {verdict.reasoning}
                </p>
                <button
                  type="button"
                  disabled={acting}
                  onClick={(e) => { e.stopPropagation(); onAct(); }}
                  className="mt-0.5 rounded-full border border-label-500/40 bg-label-500/20 px-2 py-0.5 text-[7px] font-semibold text-label-200 hover:bg-label-500/30 transition-colors disabled:opacity-50"
                >
                  {acting ? "Working…" : NEXT_ACTION_META[verdict.next_action_kind].cta}
                </button>
              </>
            ) : summary ? (
              <p className="text-[7px] leading-tight text-zinc-300 line-clamp-4 whitespace-pre-wrap">
                {summary.message}
              </p>
            ) : (
              <p className="text-[7px] text-zinc-600">No verdict yet — Dez is still listening.</p>
            )}
          </div>
        ) : (
          <div className="flex h-full flex-col items-center justify-center gap-1.5 px-3 text-center">
            {phaseInfo?.isAnalyzing ? (
              <div className="flex flex-col items-center gap-1.5">
                <Waveform />
                <p className="text-[8px] font-semibold text-emerald-300">Analyzing</p>
              </div>
            ) : (
              <>
                <span className="rounded-full border border-label-500/20 bg-label-500/10 px-1.5 py-0.5 text-[7px] font-semibold uppercase tracking-wider text-label-400">
                  {track.state.replace(/_/g, " ")}
                </span>
                <p className="text-[9px] font-semibold leading-tight text-zinc-100 line-clamp-2">
                  {track.title ?? "Untitled"}
                </p>
                {latestMessage && isAgentName(latestMessage.agent) && (
                  <div className="mt-0.5 w-full border-t border-surface-3/50 pt-1">
                    <p className="text-[7px] font-semibold text-zinc-500">
                      {AGENT_META[latestMessage.agent as AgentName].label}
                    </p>
                    <p className="mt-0.5 text-[7px] leading-tight text-zinc-400 line-clamp-3">
                      {latestMessage.message}
                    </p>
                  </div>
                )}
              </>
            )}
          </div>
        )}
      </div>

      {/* SVG overlay */}
      <svg
        className="pointer-events-none absolute inset-0 h-full w-full"
        viewBox="0 0 100 100"
        xmlns="http://www.w3.org/2000/svg"
      >
        {/* Dashed orbit ring */}
        <circle
          cx="50" cy="50" r={SEAT_RADIUS}
          fill="none"
          stroke="rgba(63,63,70,0.35)"
          strokeWidth="0.25"
          strokeDasharray="1.2 2"
        />

        {/* All unique arcs from the conversation — every path is visible */}
        {arcSet.map(({ from, to }) => {
          const fromIdx = AGENT_ORDER.indexOf(from);
          const toIdx = AGENT_ORDER.indexOf(to);
          if (fromIdx === -1 || toIdx === -1) return null;
          const d = getArcPath(seatCoords[fromIdx], seatCoords[toIdx]);
          const key = `${from}→${to}`;
          const isLatest = key === latestArcKey;
          const meta = AGENT_META[to];
          return (
            <path
              key={`arc-${key}`}
              d={d}
              fill="none"
              stroke={isLatest ? meta.svgColor : "rgba(113,113,122,0.55)"}
              strokeWidth={isLatest ? "0.55" : "0.3"}
              opacity={isLatest ? 0.6 : 0.25}
              strokeDasharray={isLatest ? undefined : "0.8 1.6"}
            />
          );
        })}

        {/* Hidden path for one-shot transition particle */}
        {particleArcPath && (
          <path id="handoff-particle-path" d={particleArcPath} fill="none" stroke="none" />
        )}

        {/* One-shot burst particle fires the instant the speaker changes */}
        {particleArcPath && particleAgents && (
          <circle key={particleKey} r="1.4" fill={AGENT_META[particleAgents.to]?.svgColor ?? "#71717a"}>
            <animateMotion
              dur="0.72s" begin="0s" fill="freeze"
              calcMode="spline" keyTimes="0;1" keySplines="0.4 0 0.2 1"
            >
              <mpath href="#handoff-particle-path" />
            </animateMotion>
            <animate
              attributeName="opacity" values="1;1;0"
              keyTimes="0;0.65;1" dur="0.72s" begin="0s" fill="freeze"
            />
          </circle>
        )}

        {/* Active loop particle: orbits the latest arc continuously while live */}
        {!idleMode && currentArc && (
          <>
            <path id="current-arc-path" d={currentArc.path} fill="none" stroke="none" />
            <circle
              key={`loop-${currentArc.toAgent}`}
              r="1.0"
              fill={currentArc.color}
              opacity="0.85"
            >
              <animateMotion
                dur="1.6s" begin="0s" repeatCount="indefinite"
                calcMode="spline" keyTimes="0;1" keySplines="0.4 0 0.6 1"
              >
                <mpath href="#current-arc-path" />
              </animateMotion>
              {/* Brief fade-out at the end hides the jump back to origin */}
              <animate
                attributeName="opacity"
                values="0;1;1;0;0"
                keyTimes="0;0.08;0.78;0.94;1"
                dur="1.6s" begin="0s" repeatCount="indefinite"
              />
            </circle>
          </>
        )}

        {/* Idle replay: particle walks the full conversation order, never stopping */}
        {idleMode && replayArc && (
          <g key={`replay-${replayKey}`}>
            <path id="replay-arc-path" d={replayArc.path} fill="none" stroke="none" />
            {/* Arc glow pulses in sync with the particle */}
            <path
              d={replayArc.path}
              fill="none"
              stroke={replayArc.color}
              strokeWidth="0.7"
              opacity="0"
            >
              <animate
                attributeName="opacity"
                values="0;0.55;0.55;0"
                keyTimes="0;0.12;0.82;1"
                dur="1.3s" begin="0s" repeatCount="indefinite"
              />
            </path>
            {/* Replay particle loops on this arc until the step advances */}
            <circle r="1.5" fill={replayArc.color}>
              <animateMotion
                dur="1.3s" begin="0s" repeatCount="indefinite"
                calcMode="spline" keyTimes="0;1" keySplines="0.25 0 0.1 1"
              >
                <mpath href="#replay-arc-path" />
              </animateMotion>
              <animate
                attributeName="opacity"
                values="0;1;1;0;0"
                keyTimes="0;0.08;0.78;0.94;1"
                dur="1.3s" begin="0s" repeatCount="indefinite"
              />
            </circle>
          </g>
        )}
      </svg>

      {/* Agent seats */}
      {AGENT_ORDER.map((agent, i) => {
        const coords = seatCoords[i];
        const meta = AGENT_META[agent];
        const history = outboundByAgent.get(agent) ?? [];
        const isActive = history.length > 0;
        const isSpeaking = agent === latestSpeaker;
        const isSelected = agent === selectedAgent;
        const isPending =
          (phaseInfo?.isPendingAgents ?? false) &&
          (phaseInfo?.agentsPending.includes(agent) ?? false);

        return (
          <button
            key={agent}
            type="button"
            style={{
              position: "absolute",
              left: `${coords.left}%`,
              top: `${coords.top}%`,
              transform: "translate(-50%, -50%)",
            }}
            disabled={!isActive && !isPending}
            onClick={(e) => {
              e.stopPropagation();
              setTableOpen(false);
              setSelectedAgent(isSelected ? null : agent);
            }}
            className={`group flex w-14 flex-col items-center gap-0.5 transition-transform duration-200 ${
              isSelected ? "scale-110" : isActive ? "hover:scale-105" : ""
            }`}
          >
            <div
              className={[
                "relative flex h-9 w-9 select-none items-center justify-center rounded-full border-2 text-[10px] font-bold transition-all duration-300",
                isActive
                  ? `${meta.border} ${meta.badge}`
                  : isPending
                    ? "border-blue-500/40 bg-blue-500/5 text-blue-400"
                    : "border-surface-3/40 bg-surface-1/20 text-zinc-700",
                isSpeaking ? "speaking-avatar" : "",
              ]
                .filter(Boolean)
                .join(" ")}
              style={
                isSpeaking
                  ? ({ "--ring-color": meta.ringColor } as React.CSSProperties)
                  : undefined
              }
            >
              {meta.initial}
              {isSpeaking && (
                <span
                  className={`live-dot absolute -right-0.5 -top-0.5 h-2 w-2 rounded-full border border-surface-0 ${meta.dot}`}
                />
              )}
              {isActive && !isSpeaking && (
                <span
                  className={`absolute -right-0.5 -top-0.5 h-1.5 w-1.5 rounded-full border border-surface-0 ${meta.dot} opacity-60`}
                />
              )}
            </div>

            <span
              className={`max-w-full truncate text-[9px] font-semibold leading-none ${
                isActive ? "text-zinc-200" : isPending ? "text-blue-400/70" : "text-zinc-700"
              }`}
            >
              {meta.label}
            </span>

            {isPending && !isActive ? (
              <TypingDots />
            ) : isActive ? (
              <span className="text-[7px] leading-none text-zinc-600">
                {history.length}{history.length === 1 ? " msg" : " msgs"}
              </span>
            ) : null}
          </button>
        );
      })}

      {/* Arc legend */}
      <div className="absolute bottom-1 left-1/2 -translate-x-1/2 flex items-center gap-1 pointer-events-none">
        <svg width="16" height="6" viewBox="0 0 16 6">
          <path d="M 0 3 Q 8 0 16 3" fill="none" stroke="rgba(113,113,122,0.5)" strokeWidth="0.8" strokeDasharray="2 2" />
        </svg>
        <span className="text-[7px] text-zinc-600">conversation flow</span>
      </div>

      {/* Agent speech bubble */}
      {selectedAgent && bubbleCoords && (() => {
        const meta = AGENT_META[selectedAgent];
        const history = outboundByAgent.get(selectedAgent) ?? [];
        return (
          <div
            style={{
              position: "absolute",
              left: `${bubbleCoords.left}%`,
              top: `${bubbleCoords.top}%`,
              transform: "translate(-50%, -50%)",
              width: "48%",
              maxHeight: "46%",
              zIndex: 20,
            }}
            onClick={(e) => e.stopPropagation()}
            className="rounded-xl border border-surface-3 bg-surface-1/95 p-2.5 shadow-2xl backdrop-blur-sm overflow-hidden flex flex-col"
          >
            <div className="mb-1.5 flex items-center justify-between gap-1">
              <div className="flex items-center gap-1">
                <span
                  className={`flex h-4 w-4 shrink-0 items-center justify-center rounded-full border text-[7px] font-bold ${meta.border} ${meta.badge}`}
                >
                  {meta.initial}
                </span>
                <p className="text-[10px] font-semibold text-zinc-200">{meta.label}</p>
              </div>
              <button
                type="button"
                onClick={(e) => { e.stopPropagation(); setSelectedAgent(null); }}
                className="shrink-0 text-[9px] text-zinc-600 hover:text-zinc-400"
              >
                ✕
              </button>
            </div>
            {history.length === 0 ? (
              <p className="text-[9px] text-zinc-600">Nothing said yet</p>
            ) : (
              <div className="min-h-0 flex-1 overflow-y-auto space-y-2 pr-0.5">
                {history.map((msg, idx) => (
                  <div key={msg.id}>
                    <div className="mb-0.5 flex items-center justify-between gap-2">
                      {history.length > 1 ? (
                        <p className="text-[8px] text-zinc-600">
                          {idx === 0 ? "First" : `Update ${idx}`}
                        </p>
                      ) : (
                        <span />
                      )}
                      <VoicePlayButton messageId={msg.id} />
                    </div>
                    <p className="text-[9px] leading-[1.45] text-zinc-300 whitespace-pre-wrap">
                      {msg.message}
                    </p>
                  </div>
                ))}
              </div>
            )}
          </div>
        );
      })()}
    </div>
  );
}
