import { useEffect, useMemo, useRef, useState } from "react";
import type { AgentName } from "../hooks/useAgentMessages";
import { isAgentName } from "../hooks/useAgentMessages";
import type { Feedback, Track } from "../lib/hermes-bridge";
import type { PhaseInfo } from "../lib/pipeline-phase";
import type { Verdict } from "../lib/verdict";
import { NEXT_ACTION_META, VERDICT_META } from "../lib/verdict";
import { AGENT_META, AGENT_ORDER } from "../lib/agents";
import { subscribeVoicePlayback } from "../lib/voice-playback";
import { subscribePlayback } from "../lib/track-playback";
import { useVoiceLevels, useTrackLevels } from "../lib/audio-visualizer";
import { useAnalysis } from "../hooks/useAnalysis";
import VoicePlayButton from "./VoicePlayButton";

const SEAT_RADIUS = 38;
const TABLE_RADIUS = 23;

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
  const [showAudioSpecs, setShowAudioSpecs] = useState(false);
  const { analysis } = useAnalysis(track?.id ?? null);

  // Live voice playback tracking
  const [voicePlayback, setVoicePlayback] = useState<{ messageId: number | null; playing: boolean }>({
    messageId: null,
    playing: false,
  });
  const [trackPlaying, setTrackPlaying] = useState(false);

  useEffect(() => {
    const unsubVoice = subscribeVoicePlayback((snap) => {
      setVoicePlayback({ messageId: snap.messageId, playing: snap.playing });
    });
    const unsubTrack = subscribePlayback((snap) => {
      setTrackPlaying(snap.playing);
    });
    return () => {
      unsubVoice();
      unsubTrack();
    };
  }, []);

  const voiceLevels = useVoiceLevels(voicePlayback.playing);
  const trackLevels = useTrackLevels(trackPlaying);

  const activeVoiceAgent = useMemo(() => {
    if (!voicePlayback.playing || voicePlayback.messageId === null) return null;
    const msg = outboundStream.find((m) => m.id === voicePlayback.messageId);
    return msg && isAgentName(msg.agent) ? (msg.agent as AgentName) : null;
  }, [voicePlayback, outboundStream]);

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
    setShowAudioSpecs(false);
  };

  return (
    <div
      className="relative w-full"
      style={{ aspectRatio: "1" }}
      onClick={dismissAll}
    >
      {/* Ambient background studio radar glow */}
      <div
        className="pointer-events-none absolute inset-0 rounded-full opacity-40 blur-3xl transition-opacity duration-700"
        style={{
          background: trackPlaying
            ? `radial-gradient(circle at 50% 50%, rgba(20, 184, 166, ${0.15 + trackLevels.energy * 0.25}) 0%, rgba(139, 92, 246, 0.08) 50%, transparent 70%)`
            : "radial-gradient(circle at 50% 50%, rgba(244, 244, 245, 0.03) 0%, transparent 60%)",
        }}
      />

      {/* Table surface (Vinyl Console - Team Verdict Core) */}
      <div
        className={`absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 overflow-hidden rounded-full border border-zinc-700/90 cursor-pointer transition-all duration-300 z-10 ${
          verdict
            ? "ring-2 ring-label-500/50 shadow-[0_0_30px_rgba(20,184,166,0.25)]"
            : "hover:border-zinc-500"
        }`}
        style={{
          width: `${TABLE_RADIUS * 2}%`,
          height: `${TABLE_RADIUS * 2}%`,
          background: trackPlaying
            ? `radial-gradient(circle at 50% 50%, rgba(20, 184, 166, ${0.1 + trackLevels.energy * 0.2}) 0%, rgba(18, 18, 20, 0.98) 75%), var(--color-surface-1, #18181b)`
            : "radial-gradient(ellipse at 35% 35%, rgba(255,255,255,0.04) 0%, rgba(0,0,0,0.2) 60%), var(--color-surface-1, #18181b)",
          boxShadow: trackPlaying
            ? `0 0 ${20 + trackLevels.energy * 35}px rgba(20, 184, 166, ${0.25 + trackLevels.energy * 0.45}), inset 0 2px 20px rgba(0,0,0,0.6), inset 0 0 0 1px rgba(255,255,255,0.1)`
            : "inset 0 2px 18px rgba(0,0,0,0.5), inset 0 0 0 1px rgba(255,255,255,0.05)",
          transform: `translate(-50%, -50%) scale(${1 + (trackPlaying ? trackLevels.energy * 0.03 : 0)})`,
        }}
        onClick={(e) => {
          e.stopPropagation();
          setSelectedAgent(null);
          setShowAudioSpecs((s) => !s);
        }}
        title="Click to toggle Team Verdict / Track Audio Specs"
      >
        {/* Vinyl record concentric grooves texture overlay */}
        <div
          className={`pointer-events-none absolute inset-0 rounded-full opacity-25 ${
            trackPlaying ? "animate-[spin_24s_linear_infinite]" : ""
          }`}
          style={{
            backgroundImage: `repeating-radial-gradient(circle at 50% 50%, transparent 0, transparent 4px, rgba(255,255,255,0.06) 5px, transparent 6px)`,
          }}
        />

        {showAudioSpecs ? (
          /* Track Audio Specs View */
          <div className="flex h-full flex-col items-center justify-center gap-1 px-3 text-center animate-in fade-in duration-150">
            <span className="rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2 py-0.5 text-[8px] font-bold uppercase tracking-wider text-emerald-300">
              Audio Specs
            </span>
            <div className="grid grid-cols-2 gap-x-2 gap-y-0.5 text-[10px] w-full text-left bg-zinc-950/70 p-2 rounded-xl border border-zinc-800/80 mt-0.5">
              <div>
                <span className="text-[8px] text-zinc-500 uppercase block font-semibold">Key</span>
                <span className="font-bold text-zinc-200">{analysis?.musical_key ?? "Auto"}</span>
              </div>
              <div>
                <span className="text-[8px] text-zinc-500 uppercase block font-semibold">BPM</span>
                <span className="font-bold text-zinc-200">{analysis?.bpm ? Math.round(analysis.bpm) : "—"}</span>
              </div>
              <div className="col-span-2 pt-0.5 border-t border-zinc-800/50">
                <span className="text-[8px] text-zinc-500 uppercase block font-semibold">Vibe</span>
                <span className="text-[9px] text-zinc-300 truncate block">
                  {Array.isArray(analysis?.mood_tags)
                    ? analysis?.mood_tags.join(", ")
                    : analysis?.mood_tags ?? "Late night"}
                </span>
              </div>
            </div>
            <span className="text-[8px] text-zinc-500">Click to return to verdict</span>
          </div>
        ) : verdict ? (
          /* Team Verdict View (Always prominent) */
          <div className="flex h-full flex-col items-center justify-center gap-1.5 px-3 text-center">
            <span className="rounded-full border border-label-500/30 bg-label-500/15 px-2.5 py-0.5 text-[9px] font-bold uppercase tracking-wider text-label-300 shadow-sm">
              {VERDICT_META[verdict.recommendation].label}
            </span>
            <p className="text-[11px] font-bold leading-tight text-zinc-100 line-clamp-2 px-1">
              {verdict.headline}
            </p>
            <p className="text-[9px] leading-snug text-zinc-400 line-clamp-2 px-1 whitespace-pre-wrap">
              {verdict.reasoning}
            </p>
            <button
              type="button"
              disabled={acting}
              onClick={(e) => {
                e.stopPropagation();
                onAct();
              }}
              className="mt-0.5 rounded-full border border-label-500/40 bg-label-500/20 px-3 py-0.5 text-[10px] font-bold text-label-200 hover:bg-label-500/30 active:scale-95 transition-all disabled:opacity-50 shadow-sm"
            >
              {acting ? "Working…" : NEXT_ACTION_META[verdict.next_action_kind].cta}
            </button>
          </div>
        ) : (
          /* Pre-Verdict / In Review View */
          <div className="flex h-full flex-col items-center justify-center gap-1.5 px-4 text-center">
            {phaseInfo?.isAnalyzing ? (
              <div className="flex flex-col items-center gap-1.5">
                <Waveform />
                <p className="text-[11px] font-bold text-emerald-300">Analyzing Audio…</p>
              </div>
            ) : (
              <>
                <span className="rounded-full border border-label-500/20 bg-label-500/10 px-2 py-0.5 text-[9px] font-semibold uppercase tracking-wider text-label-400">
                  {track.state.replace(/_/g, " ")}
                </span>
                <p className="text-[12px] font-bold leading-tight text-zinc-100 line-clamp-1">
                  {track.title ?? "Untitled"}
                </p>
                {summary ? (
                  <p className="text-[9px] leading-snug text-zinc-400 line-clamp-3 whitespace-pre-wrap">
                    {summary.message}
                  </p>
                ) : latestMessage && isAgentName(latestMessage.agent) ? (
                  <div className="mt-0.5 w-full border-t border-surface-3/50 pt-1">
                    <p className="text-[9px] font-semibold text-zinc-500">
                      {AGENT_META[latestMessage.agent as AgentName].label}
                    </p>
                    <p className="text-[9px] leading-snug text-zinc-400 line-clamp-2">
                      {latestMessage.message}
                    </p>
                  </div>
                ) : (
                  <p className="text-[9px] text-zinc-600">The room is listening.</p>
                )}
              </>
            )}
          </div>
        )}
      </div>

      {/* SVG overlay */}
      <svg
        className="pointer-events-none absolute inset-0 h-full w-full overflow-visible"
        viewBox="0 0 100 100"
        xmlns="http://www.w3.org/2000/svg"
      >
        <defs>
          <filter id="neon-glow" x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur stdDeviation="1.2" result="coloredBlur" />
            <feMerge>
              <feMergeNode in="coloredBlur" />
              <feMergeNode in="coloredBlur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>

        {/* Outer subtle concentric studio rings */}
        <circle
          cx="50" cy="50" r={SEAT_RADIUS + 7}
          fill="none"
          stroke="rgba(255,255,255,0.02)"
          strokeWidth="0.2"
        />
        <circle
          cx="50" cy="50" r={SEAT_RADIUS - 8}
          fill="none"
          stroke="rgba(255,255,255,0.03)"
          strokeWidth="0.2"
        />

        {/* Dashed orbit ring with subtle rotation when playing */}
        <circle
          cx="50" cy="50" r={SEAT_RADIUS}
          fill="none"
          stroke={trackPlaying ? "rgba(20, 184, 166, 0.4)" : "rgba(63,63,70,0.35)"}
          strokeWidth="0.3"
          strokeDasharray="1.5 2"
        />

        {/* Studio perimeter radar tick marks */}
        {[0, 30, 60, 90, 120, 150, 180, 210, 240, 270, 300, 330].map((deg) => {
          const rad = (deg * Math.PI) / 180;
          const x1 = 50 + (SEAT_RADIUS - 1.5) * Math.cos(rad);
          const y1 = 50 + (SEAT_RADIUS - 1.5) * Math.sin(rad);
          const x2 = 50 + (SEAT_RADIUS + 1.5) * Math.cos(rad);
          const y2 = 50 + (SEAT_RADIUS + 1.5) * Math.sin(rad);
          return (
            <line
              key={deg}
              x1={x1} y1={y1} x2={x2} y2={y2}
              stroke="rgba(255,255,255,0.06)"
              strokeWidth="0.25"
            />
          );
        })}

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
              stroke={isLatest ? meta.svgColor : "rgba(113,113,122,0.45)"}
              strokeWidth={isLatest ? "0.65" : "0.3"}
              opacity={isLatest ? 0.8 : 0.2}
              strokeDasharray={isLatest ? undefined : "0.8 1.6"}
              filter={isLatest ? "url(#neon-glow)" : undefined}
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
        const isVoicePlaying = activeVoiceAgent === agent;
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
              transform: `translate(-50%, -50%) scale(${isVoicePlaying ? 1 + voiceLevels.energy * 0.08 : isSelected ? 1.1 : 1})`,
            }}
            disabled={!isActive && !isPending}
            onClick={(e) => {
              e.stopPropagation();
              setShowAudioSpecs(false);
              setSelectedAgent(isSelected ? null : agent);
            }}
            className={`group flex w-20 flex-col items-center gap-1 transition-transform duration-200 ${
              isActive ? "hover:scale-105" : ""
            }`}
          >
            {/* Illuminated floor pod disc beneath seat */}
            <div
              className={`pointer-events-none absolute -bottom-1 h-3 w-14 rounded-full opacity-40 blur-sm transition-opacity duration-300 ${
                isVoicePlaying
                  ? "opacity-90 scale-125"
                  : isSpeaking
                    ? "opacity-75"
                    : isActive
                      ? "opacity-30 group-hover:opacity-75"
                      : "opacity-0"
              }`}
              style={{ backgroundColor: meta.svgColor }}
            />

            {/* Live voice visualizer mini bars above avatar when speaking */}
            {isVoicePlaying && (
              <div className="flex h-3 items-end gap-0.5 mb-0.5">
                {(voiceLevels.bands.length > 0 ? voiceLevels.bands.slice(0, 5) : [0.3, 0.6, 0.9, 0.5, 0.4]).map((b, bi) => (
                  <span
                    key={bi}
                    className="w-0.5 rounded-full transition-all duration-75"
                    style={{
                      height: `${Math.max(3, Math.min(12, b * 12))}px`,
                      backgroundColor: meta.svgColor,
                    }}
                  />
                ))}
              </div>
            )}

            <div
              className={[
                "relative flex h-12 w-12 select-none items-center justify-center rounded-full border-2 text-sm font-bold transition-all duration-200",
                isActive
                  ? `${meta.border} ${meta.badge} shadow-md`
                  : isPending
                    ? "border-blue-500/40 bg-blue-500/5 text-blue-400"
                    : "border-surface-3/40 bg-surface-1/20 text-zinc-700",
                isSpeaking ? "speaking-avatar ring-2 ring-offset-2 ring-offset-surface-0" : "",
              ]
                .filter(Boolean)
                .join(" ")}
              style={{
                ...(isSpeaking ? ({ "--ring-color": meta.ringColor } as React.CSSProperties) : {}),
                ...(isVoicePlaying
                  ? {
                      boxShadow: `0 0 ${12 + voiceLevels.energy * 24}px ${meta.svgColor}`,
                      borderColor: meta.svgColor,
                    }
                  : isActive
                    ? {
                        boxShadow: `0 2px 10px rgba(0,0,0,0.5)`,
                      }
                    : {}),
              }}
            >
              {meta.initial}
              {isVoicePlaying ? (
                <span
                  className="absolute -right-0.5 -top-0.5 flex h-2.5 w-2.5 items-center justify-center"
                >
                  <span
                    className="absolute inline-flex h-full w-full animate-ping rounded-full opacity-75"
                    style={{ backgroundColor: meta.svgColor }}
                  />
                  <span
                    className="relative inline-flex h-2 w-2 rounded-full border border-surface-0"
                    style={{ backgroundColor: meta.svgColor }}
                  />
                </span>
              ) : isSpeaking ? (
                <span
                  className={`live-dot absolute -right-0.5 -top-0.5 h-2 w-2 rounded-full border border-surface-0 ${meta.dot}`}
                />
              ) : isActive ? (
                <span
                  className={`absolute -right-0.5 -top-0.5 h-1.5 w-1.5 rounded-full border border-surface-0 ${meta.dot} opacity-60`}
                />
              ) : null}
            </div>

            <span
              className={`max-w-full truncate text-[11px] font-semibold leading-none ${
                isActive ? "text-zinc-200" : isPending ? "text-blue-400/70" : "text-zinc-700"
              }`}
            >
              {meta.label}
            </span>

            {isPending && !isActive ? (
              <TypingDots />
            ) : isActive ? (
              <span className="text-[9px] leading-none text-zinc-600">
                {history.length}{history.length === 1 ? " msg" : " msgs"}
              </span>
            ) : null}
          </button>
        );
      })}

      {/* Scattered Audio Telemetry Badges inside the center gray orbit area */}
      {analysis?.musical_key && (
        <div
          className="pointer-events-none absolute z-10 -translate-x-1/2 -translate-y-1/2 transition-all duration-300"
          style={{ left: "30%", top: "30%" }}
        >
          <span
            className="flex items-center gap-1 rounded-full border border-emerald-500/30 bg-zinc-950/85 px-2 py-0.5 text-[9px] font-bold text-emerald-400 shadow-md shadow-black/60 backdrop-blur-md"
            title="Musical Key"
          >
            🎵 {analysis.musical_key}
          </span>
        </div>
      )}

      {analysis?.bpm && (
        <div
          className="pointer-events-none absolute z-10 -translate-x-1/2 -translate-y-1/2 transition-all duration-300"
          style={{ left: "70%", top: "30%" }}
        >
          <span
            className="flex items-center gap-1 rounded-full border border-amber-500/30 bg-zinc-950/85 px-2 py-0.5 text-[9px] font-bold text-amber-400 shadow-md shadow-black/60 backdrop-blur-md"
            title="Tempo (BPM)"
          >
            ⚡ {Math.round(analysis.bpm)} BPM
          </span>
        </div>
      )}

      {analysis?.genre_tags && (
        <div
          className="pointer-events-none absolute z-10 -translate-x-1/2 -translate-y-1/2 transition-all duration-300"
          style={{ left: "30%", top: "70%" }}
        >
          <span
            className="flex items-center gap-1 rounded-full border border-purple-500/30 bg-zinc-950/85 px-2 py-0.5 text-[9px] font-bold text-purple-300 shadow-md shadow-black/60 backdrop-blur-md max-w-[130px] truncate"
            title="Genre"
          >
            🎛️ {Array.isArray(analysis.genre_tags) ? analysis.genre_tags[0] : analysis.genre_tags}
          </span>
        </div>
      )}

      {analysis?.mood_tags && (
        <div
          className="pointer-events-none absolute z-10 -translate-x-1/2 -translate-y-1/2 transition-all duration-300"
          style={{ left: "70%", top: "70%" }}
        >
          <span
            className="flex items-center gap-1 rounded-full border border-cyan-500/30 bg-zinc-950/85 px-2 py-0.5 text-[9px] font-bold text-cyan-300 shadow-md shadow-black/60 backdrop-blur-md max-w-[130px] truncate"
            title="Mood / Vibe"
          >
            🎭 {Array.isArray(analysis.mood_tags) ? analysis.mood_tags[0] : analysis.mood_tags}
          </span>
        </div>
      )}

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
              width: "60%",
              maxHeight: "58%",
              zIndex: 30,
            }}
            onClick={(e) => e.stopPropagation()}
            className="rounded-2xl border border-zinc-700 bg-zinc-900 p-3.5 shadow-[0_20px_50px_rgba(0,0,0,0.9)] ring-1 ring-white/10 overflow-hidden flex flex-col"
          >
            <div className="mb-2.5 flex items-center justify-between gap-1 border-b border-zinc-800 pb-2">
              <div className="flex items-center gap-2">
                <span
                  className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full border text-[10px] font-bold ${meta.border} ${meta.badge}`}
                >
                  {meta.initial}
                </span>
                <p className="text-sm font-bold text-zinc-100">{meta.label}</p>
                <span className="text-[10px] uppercase font-semibold text-zinc-500 tracking-wider">
                  {meta.role}
                </span>
              </div>
              <button
                type="button"
                onClick={(e) => { e.stopPropagation(); setSelectedAgent(null); }}
                className="shrink-0 rounded-full p-1 text-zinc-500 hover:bg-zinc-800 hover:text-zinc-200 transition-colors"
              >
                ✕
              </button>
            </div>
            {history.length === 0 ? (
              <p className="text-[11px] text-zinc-600">Nothing said yet</p>
            ) : (
              <div className="min-h-0 flex-1 overflow-y-auto space-y-2.5 pr-0.5">
                {history.map((msg, idx) => (
                  <div key={msg.id}>
                    <div className="mb-1 flex items-center justify-between gap-2">
                      {history.length > 1 ? (
                        <p className="text-[10px] text-zinc-600">
                          {idx === 0 ? "First" : `Update ${idx}`}
                        </p>
                      ) : (
                        <span />
                      )}
                      <VoicePlayButton messageId={msg.id} />
                    </div>
                    <p className="text-[12px] leading-relaxed text-zinc-300 whitespace-pre-wrap">
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
