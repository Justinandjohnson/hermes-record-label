import { useEffect, useMemo, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";
import type { Feedback, Track } from "../lib/hermes-bridge";
import {
  restartTrackPlayback,
  seekTrackPlayback,
  setTrackLoop,
  subscribePlayback,
  subscribePlaybackPosition,
  toggleTrackPlayback,
} from "../lib/track-playback";
import type { PlaybackPositionSnapshot } from "../lib/track-playback";
import { AGENT_META } from "../lib/agents";
import { isAgentName } from "../hooks/useAgentMessages";
import { useSegments } from "../hooks/useSegments";
import { useTrackLevels } from "../lib/audio-visualizer";

const FALLBACK_COLOR = "#a1a1aa";

interface Props {
  track: Track | null;
  messages: Feedback[];
}

function formatTime(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

function parseInlineTimestamps(message: string, duration: number): number[] {
  const matches = [...message.matchAll(/\b([0-5]?\d):([0-5]\d)\b/g)];
  const results: number[] = [];
  for (const m of matches) {
    const sec = parseInt(m[1], 10) * 60 + parseInt(m[2], 10);
    if (sec >= 0 && (duration <= 0 || sec <= duration + 5)) {
      if (!results.includes(sec)) results.push(sec);
    }
  }
  return results;
}

export interface TimestampCluster {
  id: string;
  timestampSec: number;
  messages: Feedback[];
}

function IconPlay() {
  return (
    <svg viewBox="0 0 12 12" className="h-3.5 w-3.5 fill-current translate-x-0.5">
      <path d="M3 1.5v9l7.5-4.5L3 1.5z" />
    </svg>
  );
}

function IconPause() {
  return (
    <svg viewBox="0 0 12 12" className="h-3.5 w-3.5 fill-current">
      <path d="M2.5 1.5h2.8v9H2.5zM6.7 1.5h2.8v9H6.7z" />
    </svg>
  );
}

function IconRestart() {
  return (
    <svg viewBox="0 0 14 14" className="h-4 w-4 fill-none stroke-current" strokeWidth="1.6">
      <path d="M2.5 7a4.5 4.5 0 1 0 1.3-3.2" strokeLinecap="round" />
      <path d="M3.4 1.2v2.8h2.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function IconLoop() {
  return (
    <svg viewBox="0 0 14 14" className="h-4 w-4 fill-none stroke-current" strokeWidth="1.5">
      <path d="M3.5 5h6a1.8 1.8 0 0 1 1.8 1.8v.4" strokeLinecap="round" />
      <path d="M10.5 9h-6A1.8 1.8 0 0 1 2.7 7.2v-.4" strokeLinecap="round" />
      <path d="M8.2 3.2 10 5 8.2 6.8" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M5.8 7.2 4 9l1.8 1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function markerMeta(message: Feedback) {
  if (isAgentName(message.agent)) {
    return AGENT_META[message.agent];
  }
  return null;
}

/**
 * Animated, prominent player for the focus track with interactive timestamp markers,
 * audio-reactive visualizer waveform, and hoverable agent comment popovers.
 */
export default function TrackPlayerBar({ track, messages }: Props) {
  const [playing, setPlaying] = useState(false);
  const [loading, setLoading] = useState(false);
  const [position, setPosition] = useState<PlaybackPositionSnapshot>({
    active: false,
    currentTime: 0,
    duration: 0,
    looping: false,
  });
  const [openClusterId, setOpenClusterId] = useState<string | null>(null);
  const [hoveredClusterId, setHoveredClusterId] = useState<string | null>(null);
  const [hoveredRatio, setHoveredRatio] = useState<number | null>(null);
  const draggingRef = useRef(false);
  const barRef = useRef<HTMLDivElement>(null);
  const closeTimeoutRef = useRef<number | null>(null);

  const handleMouseEnterCluster = (clusterId: string) => {
    if (closeTimeoutRef.current !== null) {
      window.clearTimeout(closeTimeoutRef.current);
      closeTimeoutRef.current = null;
    }
    setHoveredClusterId(clusterId);
  };

  const handleMouseLeaveCluster = () => {
    if (closeTimeoutRef.current !== null) {
      window.clearTimeout(closeTimeoutRef.current);
    }
    closeTimeoutRef.current = window.setTimeout(() => {
      setHoveredClusterId(null);
      closeTimeoutRef.current = null;
    }, 450);
  };

  useEffect(() => {
    return () => {
      if (closeTimeoutRef.current !== null) {
        window.clearTimeout(closeTimeoutRef.current);
      }
    };
  }, []);

  useEffect(
    () =>
      subscribePlayback((snapshot) => {
        setPlaying(snapshot.playing);
        setLoading(snapshot.loading);
      }),
    [],
  );

  useEffect(() => subscribePlaybackPosition(setPosition), []);

  const trackLevels = useTrackLevels(playing);
  const { segments } = useSegments(track?.id ?? null);
  const segmentsDuration = segments.length > 0 ? segments[segments.length - 1].end_sec : 0;
  const duration = position.duration || track?.duration_seconds || segmentsDuration || 0;
  const progress = duration > 0 ? Math.min(100, (position.currentTime / duration) * 100) : 0;

  // Group and cluster messages by timestamp (supports explicit timestamp_sec and inline mentions)
  const clusters = useMemo(() => {
    const items: Array<{ ts: number; message: Feedback }> = [];
    for (const m of messages) {
      if (!m.message || m.message.trim().length === 0) continue;
      if (m.timestamp_sec != null && m.timestamp_sec >= 0) {
        items.push({ ts: m.timestamp_sec, message: m });
      } else {
        const inlines = parseInlineTimestamps(m.message, duration);
        for (const ts of inlines) {
          items.push({ ts, message: m });
        }
      }
    }
    items.sort((a, b) => a.ts - b.ts);

    const result: TimestampCluster[] = [];
    for (const item of items) {
      const existing = result.find(
        (c) => Math.abs(c.timestampSec - item.ts) <= 1.5,
      );
      if (existing) {
        if (!existing.messages.some((m) => m.id === item.message.id)) {
          existing.messages.push(item.message);
        }
      } else {
        result.push({
          id: `cluster-${item.ts.toFixed(1)}`,
          timestampSec: item.ts,
          messages: [item.message],
        });
      }
    }
    return result;
  }, [messages, duration]);

  const totalCommentCount = useMemo(() => {
    const ids = new Set<number>();
    for (const c of clusters) {
      for (const m of c.messages) ids.add(m.id);
    }
    return ids.size;
  }, [clusters]);

  const seekToRatio = (clientX: number, element: HTMLElement) => {
    if (!position.active || duration <= 0) return;
    const rect = element.getBoundingClientRect();
    const ratio = Math.max(0, Math.min(1, (clientX - rect.left) / Math.max(1, rect.width)));
    seekTrackPlayback(ratio * duration);
  };

  const handlePointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!position.active || duration <= 0) return;
    draggingRef.current = true;
    event.currentTarget.setPointerCapture(event.pointerId);
    seekToRatio(event.clientX, event.currentTarget);
  };

  const handlePointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    const ratio = Math.max(0, Math.min(1, (event.clientX - rect.left) / Math.max(1, rect.width)));
    setHoveredRatio(ratio);

    if (draggingRef.current) {
      seekToRatio(event.clientX, event.currentTarget);
    }
  };

  const handlePointerLeave = () => {
    if (!draggingRef.current) {
      setHoveredRatio(null);
      setHoveredClusterId(null);
    }
  };

  const handlePointerUp = () => {
    draggingRef.current = false;
  };

  // Active cluster popover: priority given to hoveredCluster, then openCluster
  const activeCluster = useMemo(() => {
    if (hoveredClusterId !== null) {
      return clusters.find((c) => c.id === hoveredClusterId) ?? null;
    }
    if (openClusterId !== null) {
      return clusters.find((c) => c.id === openClusterId) ?? null;
    }
    return null;
  }, [hoveredClusterId, openClusterId, clusters]);

  return (
    <div
      className={`workspace-panel relative mb-3.5 flex shrink-0 flex-col gap-2.5 rounded-2xl border border-surface-3/80 bg-surface-1/90 p-3.5 shadow-lg backdrop-blur-md transition-all duration-300 ${
        playing ? "border-label-500/30 shadow-label-500/5" : ""
      }`}
    >
      <div className="flex items-center gap-3.5">
        {/* Transport controls */}
        <div className="flex shrink-0 items-center gap-2">
          <button
            type="button"
            disabled={!track}
            onClick={() => {
              if (track) void toggleTrackPlayback(track.id);
            }}
            className={`flex h-10 w-10 items-center justify-center rounded-full bg-label-500 text-black shadow-md transition-all duration-200 hover:scale-105 hover:bg-label-400 active:scale-95 disabled:opacity-40 ${
              playing ? "shadow-label-500/25 ring-2 ring-label-500/40" : ""
            }`}
            title={playing ? "Pause (Space)" : "Play (Space)"}
            aria-label={playing ? "Pause track" : "Play track"}
          >
            {loading ? (
              <span className="block h-4 w-4 animate-spin rounded-full border-2 border-black/30 border-t-black" />
            ) : playing ? (
              <IconPause />
            ) : (
              <IconPlay />
            )}
          </button>

          <button
            type="button"
            disabled={!position.active}
            onClick={() => restartTrackPlayback()}
            className="flex h-8 w-8 items-center justify-center rounded-full text-zinc-400 transition-colors hover:bg-surface-2 hover:text-zinc-100 active:scale-95 disabled:opacity-40"
            title="Restart track"
            aria-label="Restart track"
          >
            <IconRestart />
          </button>

          <button
            type="button"
            onClick={() => setTrackLoop(!position.looping)}
            className={`flex h-8 w-8 items-center justify-center rounded-full transition-colors ${
              position.looping
                ? "bg-label-500/20 text-label-300 ring-1 ring-label-500/40"
                : "text-zinc-500 hover:bg-surface-2 hover:text-zinc-200"
            }`}
            title={position.looping ? "Loop on" : "Loop off"}
            aria-label="Toggle loop"
            aria-pressed={position.looping}
          >
            <IconLoop />
          </button>
        </div>

        {/* Track Title and Time */}
        <div className="min-w-0 flex-1">
          <div className="mb-1.5 flex items-baseline justify-between gap-3">
            <div className="flex items-center gap-2 min-w-0">
              <p className="truncate text-sm font-bold text-zinc-100">
                {track?.title ?? "No track selected"}
              </p>
              {totalCommentCount > 0 && (
                <span className="rounded-full bg-surface-3/80 px-2.5 py-0.5 text-[10px] font-semibold text-zinc-300 shrink-0">
                  {totalCommentCount} {totalCommentCount === 1 ? "comment" : "comments"}
                </span>
              )}
            </div>

            <div className="flex items-center gap-1.5 shrink-0 font-mono text-xs">
              <span className="font-semibold text-zinc-200">{formatTime(position.currentTime)}</span>
              <span className="text-zinc-600">/</span>
              <span className="text-zinc-400">{formatTime(duration)}</span>
            </div>
          </div>

          {/* Interactive Progress Bar with Waveform & Markers */}
          <div
            ref={barRef}
            className={`relative flex h-6 items-center select-none ${
              position.active && duration > 0 ? "cursor-pointer" : "cursor-default"
            }`}
            onPointerDown={handlePointerDown}
            onPointerMove={handlePointerMove}
            onPointerLeave={handlePointerLeave}
            onPointerUp={handlePointerUp}
            onPointerCancel={handlePointerUp}
          >
            {/* Background track bar */}
            <div className="relative h-2.5 w-full overflow-hidden rounded-full bg-surface-3/60 transition-all duration-150">
              {/* Audio visualizer live waveform bars when playing */}
              {playing && trackLevels.bands.length > 0 && (
                <div className="absolute inset-0 flex items-center justify-around opacity-30 pointer-events-none px-1">
                  {trackLevels.bands.map((band, idx) => (
                    <span
                      key={idx}
                      className="w-1 rounded-full bg-label-400 transition-all duration-75"
                      style={{ height: `${Math.max(15, band * 100)}%` }}
                    />
                  ))}
                </div>
              )}

              {/* Played progress fill */}
              <div
                className="relative h-full rounded-full bg-gradient-to-r from-label-600 to-label-400 transition-[width] duration-100"
                style={{ width: `${progress}%` }}
              >
                {/* Glow bar tip */}
                {playing && (
                  <div className="absolute right-0 top-0 h-full w-2 bg-white/40 blur-[1px]" />
                )}
              </div>
            </div>

            {/* Glowing playhead dot */}
            {duration > 0 && (
              <div
                className={`pointer-events-none absolute top-1/2 -translate-x-1/2 -translate-y-1/2 transition-transform ${
                  playing ? "scale-110" : ""
                }`}
                style={{ left: `${progress}%` }}
              >
                <span className="block h-3.5 w-3.5 rounded-full border-2 border-surface-0 bg-white shadow-md shadow-black/50 ring-2 ring-label-500/50" />
              </div>
            )}

            {/* Hover scrub line indicator */}
            {hoveredRatio !== null && duration > 0 && (
              <div
                className="pointer-events-none absolute top-0 bottom-0 w-0.5 -translate-x-1/2 bg-zinc-400/70"
                style={{ left: `${hoveredRatio * 100}%` }}
              />
            )}

            {/* Timestamp comment cluster markers */}
            {duration > 0 &&
              clusters.map((cluster) => {
                const markerTs = cluster.timestampSec;
                const left = Math.max(0, Math.min(100, (markerTs / duration) * 100));
                const isHovered = cluster.id === hoveredClusterId;
                const isOpen = cluster.id === openClusterId;
                const isNearPlayhead = playing && Math.abs(position.currentTime - markerTs) < 1.0;
                const leadMessage = cluster.messages[0];
                const leadMeta = markerMeta(leadMessage);
                const leadColor = leadMeta?.svgColor ?? FALLBACK_COLOR;
                const count = cluster.messages.length;

                return (
                  <div
                    key={cluster.id}
                    className="absolute top-1/2 -translate-x-1/2 -translate-y-1/2 z-20"
                    style={{ left: `${left}%` }}
                    onMouseEnter={() => handleMouseEnterCluster(cluster.id)}
                    onMouseLeave={handleMouseLeaveCluster}
                  >
                    <button
                      type="button"
                      className="group/marker relative flex h-8 w-8 items-center justify-center p-0"
                      title={`${count} comment${count > 1 ? "s" : ""} @ ${formatTime(markerTs)}`}
                      aria-label={`Comments at ${formatTime(markerTs)}`}
                      onPointerDown={(e) => e.stopPropagation()}
                      onClick={(e) => {
                        e.stopPropagation();
                        if (position.active && duration > 0) seekTrackPlayback(markerTs);
                        setOpenClusterId(isOpen ? null : cluster.id);
                      }}
                    >
                      {/* Active pulse wave when playhead crosses the marker */}
                      {isNearPlayhead && (
                        <span
                          className="absolute inline-flex h-6 w-6 animate-ping rounded-full opacity-75"
                          style={{ backgroundColor: leadColor }}
                        />
                      )}

                      {/* Marker dot with agent color & count badge */}
                      <div
                        className={`relative flex items-center justify-center rounded-full border-2 border-surface-0 shadow-md transition-all duration-200 ${
                          isHovered || isOpen
                            ? "h-5 min-w-[20px] px-1 scale-125 ring-2 ring-white/60"
                            : "h-4 min-w-[16px] px-0.5 group-hover/marker:scale-110"
                        }`}
                        style={{
                          backgroundColor: leadColor,
                          boxShadow: isHovered || isOpen ? `0 0 12px ${leadColor}` : undefined,
                        }}
                      >
                        {count > 1 ? (
                          <span className="text-[9px] font-bold text-black leading-none">
                            {count}
                          </span>
                        ) : (
                          <span className="text-[8px] font-bold text-black leading-none uppercase">
                            {leadMeta?.initial ?? "•"}
                          </span>
                        )}
                      </div>
                    </button>
                  </div>
                );
              })}
          </div>
        </div>
      </div>

      {/* Floating Interactive Comment Popover (opens downward below player bar) */}
      {activeCluster && (() => {
        const msgTs = activeCluster.timestampSec;
        const leftPercent = duration > 0 ? Math.max(25, Math.min(75, (msgTs / duration) * 100)) : 50;

        return (
          <div
            className="absolute top-full z-50 mt-3 w-[400px] max-w-[calc(100vw-40px)] -translate-x-1/2 rounded-2xl border border-zinc-700 bg-zinc-900 p-4 shadow-[0_20px_60px_rgba(0,0,0,0.95)] ring-1 ring-white/10"
            style={{ left: `${leftPercent}%` }}
            onPointerDown={(e) => e.stopPropagation()}
            onMouseEnter={() => handleMouseEnterCluster(activeCluster.id)}
            onMouseLeave={handleMouseLeaveCluster}
          >
            {/* Invisible hover bridge connecting timeline marker to the popover */}
            <div className="absolute -top-4 inset-x-0 h-4 bg-transparent pointer-events-auto" />
            <div className="mb-3 flex items-center justify-between gap-2 border-b border-zinc-800 pb-2.5">
              <div className="flex items-center gap-2">
                <span className="rounded-md bg-label-500/20 px-2 py-0.5 font-mono text-xs font-bold text-label-300">
                  @ {formatTime(msgTs)}
                </span>
                <span className="text-xs font-medium text-zinc-400">
                  {activeCluster.messages.length} {activeCluster.messages.length === 1 ? "comment" : "comments"}
                </span>
              </div>
              <div className="flex items-center gap-2">
                {position.active && duration > 0 && (
                  <button
                    type="button"
                    onClick={() => seekTrackPlayback(msgTs)}
                    className="text-xs text-label-400 hover:text-label-300 px-2 py-0.5 rounded bg-zinc-800 hover:bg-zinc-700 transition-colors font-semibold"
                    title="Jump to this moment in track"
                  >
                    Seek ↷
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => {
                    setOpenClusterId(null);
                    setHoveredClusterId(null);
                  }}
                  className="rounded-full p-1 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100 transition-colors"
                  aria-label="Close popover"
                >
                  ✕
                </button>
              </div>
            </div>

            <div className="max-h-80 overflow-y-auto space-y-3 pr-1">
              {activeCluster.messages.map((message) => {
                const meta = markerMeta(message);
                const isInbound = message.direction === "inbound";
                const who = isInbound
                  ? `You → ${meta?.label ?? message.agent}`
                  : meta?.label ?? message.agent;
                const color = meta?.svgColor ?? FALLBACK_COLOR;

                return (
                  <div
                    key={message.id}
                    className="rounded-xl border border-zinc-800 bg-zinc-950 p-3.5 text-xs leading-relaxed transition-colors hover:border-zinc-700 shadow-sm"
                  >
                    <div className="mb-1.5 flex items-center gap-2">
                      <span
                        className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[10px] font-bold text-black"
                        style={{ backgroundColor: color }}
                      >
                        {meta?.initial ?? "•"}
                      </span>
                      <p className="truncate font-bold text-zinc-100">{who}</p>
                      {message.intent && (
                        <span className="text-[9px] uppercase tracking-wider text-zinc-500 font-medium">
                          {message.intent.replace(/_/g, " ")}
                        </span>
                      )}
                    </div>
                    <p className="whitespace-pre-wrap text-zinc-300 leading-normal">{message.message}</p>
                  </div>
                );
              })}
            </div>
          </div>
        );
      })()}
    </div>
  );
}
