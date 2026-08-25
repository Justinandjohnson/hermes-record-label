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

function IconPlay() {
  return (
    <svg viewBox="0 0 12 12" className="h-3 w-3 fill-current">
      <path d="M3 1.5v9l7.5-4.5L3 1.5z" />
    </svg>
  );
}

function IconPause() {
  return (
    <svg viewBox="0 0 12 12" className="h-3 w-3 fill-current">
      <path d="M2.5 1.5h2.8v9H2.5zM6.7 1.5h2.8v9H6.7z" />
    </svg>
  );
}

function IconRestart() {
  return (
    <svg viewBox="0 0 14 14" className="h-3.5 w-3.5 fill-none stroke-current" strokeWidth="1.6">
      <path d="M2.5 7a4.5 4.5 0 1 0 1.3-3.2" strokeLinecap="round" />
      <path d="M3.4 1.2v2.8h2.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function IconLoop() {
  return (
    <svg viewBox="0 0 14 14" className="h-3.5 w-3.5 fill-none stroke-current" strokeWidth="1.5">
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
 * Persistent player for the focus track: play/pause, restart, loop, scrubbable
 * progress bar, and colored markers where comments were tagged to the track.
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
  const [openMarkerId, setOpenMarkerId] = useState<number | null>(null);
  const draggingRef = useRef(false);

  useEffect(
    () =>
      subscribePlayback((snapshot) => {
        setPlaying(snapshot.playing);
        setLoading(snapshot.loading);
      }),
    [],
  );

  useEffect(() => subscribePlaybackPosition(setPosition), []);

  // Latest comment per timestamp keeps the bar readable when several land together.
  const taggedMessages = useMemo(() => {
    const byTimestamp = new Map<number, Feedback>();
    for (const message of messages) {
      const ts = message.timestamp_sec;
      if (ts == null || ts <= 0) continue;
      const existing = byTimestamp.get(ts);
      if (!existing || message.id > existing.id) byTimestamp.set(ts, message);
    }
    return [...byTimestamp.entries()]
      .sort((a, b) => a[0] - b[0])
      .map(([, message]) => message);
  }, [messages]);

  const duration = position.duration || track?.duration_seconds || 0;
  const progress = duration > 0 ? Math.min(100, (position.currentTime / duration) * 100) : 0;

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
    setOpenMarkerId(null);
    seekToRatio(event.clientX, event.currentTarget);
  };

  const handlePointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!draggingRef.current) return;
    seekToRatio(event.clientX, event.currentTarget);
  };

  const handlePointerUp = () => {
    draggingRef.current = false;
  };

  const openMarker =
    openMarkerId !== null
      ? taggedMessages.find((m) => m.id === openMarkerId) ?? null
      : null;

  return (
    <div className="workspace-panel relative mb-3 flex shrink-0 items-center gap-3 px-3 py-2.5">
      {/* Transport */}
      <div className="flex shrink-0 items-center gap-1.5">
        <button
          type="button"
          disabled={!track}
          onClick={() => { if (track) void toggleTrackPlayback(track.id); }}
          className="flex h-8 w-8 items-center justify-center rounded-full bg-label-500 text-black transition-colors hover:bg-label-600 disabled:opacity-40"
          title={playing ? "Pause" : "Play"}
          aria-label={playing ? "Pause track" : "Play track"}
        >
          {loading ? (
            <span className="block h-3 w-3 animate-spin rounded-full border-[1.5px] border-black/30 border-t-black" />
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
          className="flex h-7 w-7 items-center justify-center rounded-full text-zinc-400 transition-colors hover:bg-surface-2 hover:text-zinc-100 disabled:opacity-40"
          title="Restart track"
          aria-label="Restart track"
        >
          <IconRestart />
        </button>
        <button
          type="button"
          onClick={() => setTrackLoop(!position.looping)}
          className={`flex h-7 w-7 items-center justify-center rounded-full transition-colors ${
            position.looping
              ? "bg-label-500/20 text-label-300"
              : "text-zinc-500 hover:bg-surface-2 hover:text-zinc-100"
          }`}
          title={position.looping ? "Loop on" : "Loop off"}
          aria-label="Toggle loop"
          aria-pressed={position.looping}
        >
          <IconLoop />
        </button>
      </div>

      {/* Progress */}
      <div className="min-w-0 flex-1">
        <div className="mb-1 flex items-baseline justify-between gap-2">
          <p className="truncate text-xs font-semibold text-zinc-200">
            {track?.title ?? "No track selected"}
          </p>
          <p className="shrink-0 font-mono text-[10px] text-zinc-500">
            {formatTime(position.currentTime)} / {formatTime(duration)}
          </p>
        </div>
        <div
          className={`relative h-4 ${position.active && duration > 0 ? "cursor-pointer" : "cursor-default"}`}
          onPointerDown={handlePointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={handlePointerUp}
          onPointerCancel={handlePointerUp}
        >
          <div className="absolute inset-x-0 top-1/2 h-1.5 -translate-y-1/2 overflow-hidden rounded-full bg-surface-3">
            <div
              className="h-full rounded-full bg-label-500 transition-[width] duration-150"
              style={{ width: `${progress}%` }}
            />
          </div>
          {/* Agent comment markers */}
          {duration > 0 &&
            taggedMessages.map((message) => {
              const meta = markerMeta(message);
              const color = meta?.svgColor ?? FALLBACK_COLOR;
              const left = Math.max(0, Math.min(100, ((message.timestamp_sec ?? 0) / duration) * 100));
              const isOpen = message.id === openMarkerId;
              return (
                <button
                  key={message.id}
                  type="button"
                  className="absolute top-1/2 z-10 flex h-4 w-4 -translate-x-1/2 -translate-y-1/2 items-center justify-center"
                  style={{ left: `${left}%` }}
                  title={`${meta?.label ?? message.agent} @ ${formatTime(message.timestamp_sec ?? 0)}`}
                  aria-label={`Comment at ${formatTime(message.timestamp_sec ?? 0)}`}
                  onPointerDown={(e) => e.stopPropagation()}
                  onClick={(e) => {
                    e.stopPropagation();
                    if (position.active && duration > 0) seekTrackPlayback(message.timestamp_sec ?? 0);
                    setOpenMarkerId(isOpen ? null : message.id);
                  }}
                >
                  <span
                    className={`block h-2.5 w-2.5 rounded-full border border-surface-0 transition-transform ${
                      isOpen ? "scale-125" : "hover:scale-125"
                    }`}
                    style={{ background: color }}
                  />
                </button>
              );
            })}
        </div>
      </div>

      {/* Tagged comment popover */}
      {openMarker && (() => {
        const meta = markerMeta(openMarker);
        const isInbound = openMarker.direction === "inbound";
        const who = isInbound
          ? `You → ${meta?.label ?? openMarker.agent}`
          : meta?.label ?? openMarker.agent;
        return (
          <div
            className="absolute bottom-full left-1/2 z-30 mb-2 w-80 max-w-[90vw] -translate-x-1/2 rounded-xl border border-surface-3 bg-surface-1 p-3 shadow-2xl"
            onPointerDown={(e) => e.stopPropagation()}
          >
            <div className="mb-1.5 flex items-center justify-between gap-2">
              <div className="flex min-w-0 items-center gap-1.5">
                <span
                  className="h-2 w-2 shrink-0 rounded-full"
                  style={{ background: meta?.svgColor ?? FALLBACK_COLOR }}
                />
                <p className="truncate text-[11px] font-semibold text-zinc-200">{who}</p>
                <span className="shrink-0 font-mono text-[9px] text-zinc-600">
                  @ {formatTime(openMarker.timestamp_sec ?? 0)}
                </span>
              </div>
              <button
                type="button"
                onClick={() => setOpenMarkerId(null)}
                className="shrink-0 text-[11px] text-zinc-600 hover:text-zinc-300"
                aria-label="Close comment"
              >
                ✕
              </button>
            </div>
            <p className="max-h-32 overflow-y-auto whitespace-pre-wrap text-[11px] leading-relaxed text-zinc-300">
              {openMarker.message}
            </p>
          </div>
        );
      })()}
    </div>
  );
}
