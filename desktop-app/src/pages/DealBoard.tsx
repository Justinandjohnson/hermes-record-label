import { useState } from "react";
import { useTracks } from "../hooks/useHermesDB";
import { useAgentMessages } from "../hooks/useAgentMessages";
import {
  RELEASE_STATES,
  STATE_LABELS,
  STATE_COLORS,
  TRANSITIONS,
} from "../lib/state-machine";
import type { ReleaseState } from "../lib/state-machine";
import { deleteTrackTracking, transitionTrackState, vaultTrack } from "../lib/hermes-bridge";
import type { Track } from "../lib/hermes-bridge";
import StatePipeline from "../components/StatePipeline";
import PostDropFlowStatus from "../components/PostDropFlowStatus";
import TrackPlaybackButton from "../components/TrackPlaybackButton";
import RoundtableReview from "../components/RoundtableReview";

// ── Track Detail Drawer ───────────────────────────────────────────────────────

function TrackDrawer({
  track,
  onClose,
  onTransition,
  onVault,
  onDelete,
}: {
  track: Track;
  onClose: () => void;
  onTransition: (toState: ReleaseState) => Promise<void>;
  onVault: () => Promise<void>;
  onDelete: () => Promise<void>;
}) {
  const { messages } = useAgentMessages(track.id);
  const state = track.state as ReleaseState;
  const nextStates = TRANSITIONS[state] ?? [];
  const [savingState, setSavingState] = useState<ReleaseState | null>(null);
  const [archiving, setArchiving] = useState<"vault" | "delete" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleTransition = async (toState: ReleaseState) => {
    if (savingState) return;
    setSavingState(toState);
    setError(null);
    try {
      await onTransition(toState);
    } catch (err) {
      setError(err instanceof Error ? err.message : "State update failed");
    } finally {
      setSavingState(null);
    }
  };

  const handleArchive = async (mode: "vault" | "delete") => {
    if (archiving) return;
    setArchiving(mode);
    setError(null);
    try {
      if (mode === "vault") {
        await onVault();
      } else {
        await onDelete();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Track action failed");
    } finally {
      setArchiving(null);
    }
  };

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-black/40 z-30"
        onClick={onClose}
      />

      {/* Drawer panel */}
      <div className="fixed right-0 top-0 bottom-0 w-96 bg-surface-1 border-l border-surface-3 z-40 flex flex-col shadow-2xl">
        {/* Header */}
        <div className="flex items-center gap-3 p-4 border-b border-surface-3 shrink-0">
          <button
            onClick={onClose}
            className="btn-ghost p-2 text-xs"
            title="Close"
          >
            ✕
          </button>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 min-w-0">
              <h2 className="font-semibold text-zinc-100 truncate min-w-0">
                {track.title ?? "Untitled Track"}
              </h2>
              <TrackPlaybackButton trackId={track.id} compact />
            </div>
            <div className="flex items-center gap-2 mt-0.5">
              {track.format && (
                <span className="text-[10px] font-mono text-zinc-500 uppercase">
                  {track.format}
                </span>
              )}
              {track.version > 1 && (
                <span className="text-[10px] text-label-500">
                  v{track.version}
                </span>
              )}
            </div>
          </div>
          <span
            className={`state-badge text-white ${
              STATE_COLORS[state] ?? "bg-zinc-600"
            }`}
          >
            {STATE_LABELS[state] ?? state}
          </span>
        </div>

        {/* State pipeline */}
        <div className="px-4 py-3 border-b border-surface-3 shrink-0">
          <p className="text-[10px] text-zinc-600 uppercase tracking-wide mb-2">
            Pipeline
          </p>
          <StatePipeline currentState={state} />
        </div>

        <div className="px-4 py-3 border-b border-surface-3 shrink-0">
          <PostDropFlowStatus
            track={track}
            messages={messages}
            variant="full"
          />
        </div>

        {/* Next transitions */}
        {nextStates.length > 0 && (
          <div className="px-4 py-3 border-b border-surface-3 shrink-0">
            <p className="text-[10px] text-zinc-600 uppercase tracking-wide mb-2">
              Move to
            </p>
            <div className="flex gap-2 flex-wrap">
              {nextStates.map((ns) => (
                <button
                  key={ns}
                  className={`text-xs px-3 py-1.5 rounded-lg text-white font-medium transition-opacity hover:opacity-80 ${
                    STATE_COLORS[ns] ?? "bg-zinc-600"
                  }`}
                  disabled={savingState !== null}
                  title={`Move to ${STATE_LABELS[ns]}`}
                  onClick={() => void handleTransition(ns)}
                >
                  {savingState === ns ? "Moving..." : STATE_LABELS[ns]}
                </button>
              ))}
            </div>
            {error && (
              <p className="text-xs text-red-400 mt-2">{error}</p>
            )}
          </div>
        )}

        <div className="px-4 py-3 border-b border-surface-3 shrink-0">
          <p className="text-[10px] text-zinc-600 uppercase tracking-wide mb-2">
            Cleanup
          </p>
          <div className="flex gap-2">
            <button
              className="btn-ghost text-xs px-3 py-1.5"
              disabled={archiving !== null}
              onClick={() => void handleArchive("vault")}
            >
              {archiving === "vault" ? "Vaulting..." : "Vault"}
            </button>
            <button
              className="btn-ghost text-xs px-3 py-1.5 text-red-300 hover:text-red-200"
              disabled={archiving !== null}
              onClick={() => void handleArchive("delete")}
            >
              {archiving === "delete" ? "Deleting..." : "Delete"}
            </button>
          </div>
          {error && (
            <p className="text-xs text-red-400 mt-2">{error}</p>
          )}
        </div>

        {/* Roundtable */}
        <div className="flex-1 overflow-y-auto p-4">
          <RoundtableReview
            track={track}
            messages={messages}
            title="Track roundtable"
          />
        </div>
      </div>
    </>
  );
}

// ── Column card ───────────────────────────────────────────────────────────────

function ColumnCard({
  track,
  onClick,
}: {
  track: Track;
  onClick: () => void;
}) {
  const { messages } = useAgentMessages(track.id);
  const state = track.state as ReleaseState;
  const nextStates = TRANSITIONS[state] ?? [];
  const latestFeedback =
    [...messages].reverse().find(
      (message) =>
        message.direction === "outbound" &&
        message.intent !== "studio_queue_delivery",
    ) ??
    [...messages].reverse().find((message) => message.direction === "outbound");
  const relativeTime = (() => {
    const diff = Date.now() - new Date(track.updated_at).getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return "just now";
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    return `${Math.floor(hrs / 24)}d ago`;
  })();

  return (
    <div
      className={`bg-surface-2 border border-surface-3 rounded-lg p-3 text-sm cursor-pointer hover:border-zinc-500 hover:bg-surface-3/50 transition-all group ${
        state === "IN_REVIEW" ? "analyzing-card" : ""
      }`}
      onClick={onClick}
    >
      <div className="flex items-center gap-2">
        <p className="font-medium text-zinc-200 truncate group-hover:text-label-400 transition-colors min-w-0 flex-1">
          {track.title ?? "Untitled"}
        </p>
        <TrackPlaybackButton trackId={track.id} compact />
      </div>
      <div className="flex items-center gap-2 mt-1.5 text-[10px] text-zinc-500">
        {track.format && (
          <span className="uppercase bg-surface-3 px-1.5 py-0.5 rounded font-mono">
            {track.format}
          </span>
        )}
        {track.version > 1 && (
          <span className="text-label-500">v{track.version}</span>
        )}
        <span className="ml-auto">{relativeTime}</span>
      </div>
      {latestFeedback && (
        <div className="mt-2 rounded-md border border-surface-3/80 bg-surface-1/70 px-2 py-2">
          <p className="text-[9px] font-semibold uppercase tracking-wide text-zinc-500">
            Latest feedback
          </p>
          <p className="mt-1 max-h-16 overflow-hidden text-[11px] leading-4 text-zinc-300">
            {latestFeedback.message}
          </p>
        </div>
      )}
      {/* Next state hint */}
      {nextStates.length > 0 && (
        <div className="mt-2 flex gap-1 flex-wrap">
          {nextStates.slice(0, 2).map((ns) => (
            <span
              key={ns}
              className="text-[9px] px-1.5 py-0.5 rounded-full text-white/70 opacity-60"
              style={{ background: "rgba(63,63,70,0.8)" }}
            >
              → {STATE_LABELS[ns]}
            </span>
          ))}
        </div>
      )}
      <PostDropFlowStatus track={track} messages={messages} />
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function DealBoard() {
  const { tracks, loading, refresh } = useTracks();
  const [selectedTrack, setSelectedTrack] = useState<Track | null>(null);

  const grouped = RELEASE_STATES.reduce(
    (acc, state) => {
      acc[state] = tracks.filter((t) => t.state === state);
      return acc;
    },
    {} as Record<ReleaseState, Track[]>,
  );

  const handleTransition = async (toState: ReleaseState) => {
    if (!selectedTrack) return;
    const updated = await transitionTrackState(selectedTrack.id, toState);
    setSelectedTrack(updated);
    await refresh();
  };

  const handleVault = async () => {
    if (!selectedTrack) return;
    await vaultTrack(selectedTrack.id);
    setSelectedTrack(null);
    await refresh();
  };

  const handleDelete = async () => {
    if (!selectedTrack) return;
    await deleteTrackTracking(selectedTrack.id);
    setSelectedTrack(null);
    await refresh();
  };

  return (
    <div className="p-6 h-full flex flex-col">
      <div className="flex items-center justify-between mb-6 shrink-0">
        <h1 className="text-2xl font-bold text-zinc-100">Deal Board</h1>
        {loading && (
          <span className="text-xs text-zinc-600 animate-pulse">
            Loading…
          </span>
        )}
      </div>

      <div className="flex-1 overflow-x-auto">
        <div className="flex gap-3 min-w-max h-full pb-4">
          {RELEASE_STATES.map((state) => {
            const count = grouped[state].length;
            return (
              <div key={state} className="w-52 shrink-0 flex flex-col">
                {/* Column header */}
                <div className="flex items-center gap-2 mb-3 px-1">
                  <div
                    className={`w-2 h-2 rounded-full ${STATE_COLORS[state]}`}
                  />
                  <span className="text-xs font-semibold text-zinc-400 uppercase tracking-wide truncate">
                    {STATE_LABELS[state]}
                  </span>
                  <span
                    className={`text-[10px] ml-auto px-1.5 py-0.5 rounded-full font-mono ${
                      count > 0
                        ? "bg-surface-2 text-zinc-400"
                        : "text-zinc-700"
                    }`}
                  >
                    {count}
                  </span>
                </div>

                {/* Column body */}
                <div className="flex-1 bg-surface-1 border border-surface-3 rounded-xl p-2 space-y-2 overflow-y-auto">
                  {count === 0 ? (
                    <p className="text-[10px] text-zinc-700 text-center py-4">
                      Empty
                    </p>
                  ) : (
                    grouped[state].map((track) => (
                      <ColumnCard
                        key={track.id}
                        track={track}
                        onClick={() => setSelectedTrack(track)}
                      />
                    ))
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Slide-in drawer */}
      {selectedTrack && (
        <TrackDrawer
          track={selectedTrack}
          onClose={() => setSelectedTrack(null)}
          onTransition={handleTransition}
          onVault={handleVault}
          onDelete={handleDelete}
        />
      )}
    </div>
  );
}
