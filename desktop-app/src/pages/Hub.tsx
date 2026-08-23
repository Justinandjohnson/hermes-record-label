import { useEffect, useRef, useState } from "react";
import { useTracks } from "../hooks/useHermesDB";
import TrackCard from "../components/TrackCard";
import { useAgentMessages } from "../hooks/useAgentMessages";
import { deleteTrackTracking, transitionTrackState, vaultTrack } from "../lib/hermes-bridge";
import RoundtableReview from "../components/RoundtableReview";
import { collectAudioFromDrop, uploadFiles } from "../lib/intake";

function trackPriority(state: string): number {
  switch (state) {
    case "FEEDBACK_GIVEN": return 0;
    case "ART_NEEDED":     return 1;
    case "IN_REVIEW":      return 2;
    case "APPROVED":       return 3;
    case "ART_SUBMITTED":  return 4;
    case "ART_APPROVED":   return 5;
    case "RELEASE_READY":  return 6;
    case "PREFLIGHT":      return 7;
    case "UPLOADING":      return 8;
    case "DRAFT":          return 9;
    default:               return 10;
  }
}

export default function Hub() {
  const { tracks, loading: tracksLoading, refresh: refreshTracks } = useTracks();
  const [selectedTrackId, setSelectedTrackId] = useState<number | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const dragCounter = useRef(0);
  const [intakeState, setIntakeState] = useState<"idle" | "uploading" | "done" | "error">("idle");
  const [intakeProgress, setIntakeProgress] = useState(0);
  const [intakeName, setIntakeName] = useState("");
  const [intakeError, setIntakeError] = useState("");

  useEffect(() => {
    const prevent = (e: DragEvent) => e.preventDefault();
    document.addEventListener("dragover", prevent);
    document.addEventListener("drop", prevent);
    return () => {
      document.removeEventListener("dragover", prevent);
      document.removeEventListener("drop", prevent);
    };
  }, []);

  useEffect(() => {
    if (intakeState !== "done" || tracks.length === 0) return;
    const newest = [...tracks].sort(
      (a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime(),
    )[0];
    setSelectedTrackId(newest.id);
    setIntakeState("idle");
  }, [intakeState, tracks]);

  const handleDrop = async (e: React.DragEvent) => {
    e.preventDefault();
    dragCounter.current = 0;
    setDragOver(false);
    const { files, name } = await collectAudioFromDrop(e.dataTransfer);
    if (files.length === 0) return;
    setIntakeName(name);
    setIntakeState("uploading");
    setIntakeProgress(0);
    setIntakeError("");
    try {
      await uploadFiles(files, name, (pct) => setIntakeProgress(pct));
      await refreshTracks();
      setIntakeState("done");
    } catch (error) {
      setIntakeError(error instanceof Error ? error.message : String(error));
      setIntakeState("error");
    }
  };

  const activeTracks = tracks.filter((t) => t.state !== "RELEASED" && t.state !== "VAULT");
  const sortedActiveTracks = [...activeTracks].sort((a, b) => {
    const priorityDelta = trackPriority(a.state) - trackPriority(b.state);
    if (priorityDelta !== 0) return priorityDelta;
    return new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime();
  });
  const preferredTrack = sortedActiveTracks[0];
  const trackIdForAgent = selectedTrackId ?? preferredTrack?.id ?? null;
  const { messages, loading: messagesLoading, refresh: refreshMessages } = useAgentMessages(trackIdForAgent);
  const selectedTrack = activeTracks.find((t) => t.id === trackIdForAgent) ?? null;

  const handleVault = async (trackId: number) => {
    await vaultTrack(trackId);
    if (selectedTrackId === trackId) setSelectedTrackId(null);
    await refreshTracks();
  };

  const handleDelete = async (trackId: number) => {
    await deleteTrackTracking(trackId);
    if (selectedTrackId === trackId) setSelectedTrackId(null);
    await refreshTracks();
  };

  const handleApprove = async (trackId: number) => {
    await transitionTrackState(trackId, "APPROVED");
    await refreshTracks();
  };

  return (
    <div
      className="flex h-full min-h-0 w-full flex-col overflow-hidden bg-surface-0 relative"
      onDragEnter={(e) => { e.preventDefault(); dragCounter.current++; setDragOver(true); }}
      onDragOver={(e) => e.preventDefault()}
      onDragLeave={() => { dragCounter.current--; if (dragCounter.current === 0) setDragOver(false); }}
      onDrop={(e) => void handleDrop(e)}
    >
      <div className="mx-auto flex h-full min-h-0 w-full max-w-[1800px] flex-col overflow-hidden px-3 py-3 lg:px-4">
        <div className="mb-3 flex shrink-0 items-center justify-between gap-4 border-b border-surface-3 pb-3">
          <div className="min-w-0">
            <div className="text-[11px] font-semibold uppercase tracking-[0.22em] text-zinc-500">
              Label floor
            </div>
            <h1 className="mt-1 text-lg font-semibold text-zinc-100">Roundtable</h1>
          </div>
          <div className="flex items-center gap-2">
            <div className="hidden rounded-lg border border-surface-3 bg-surface-1/80 px-3 py-2 text-right md:block">
              <div className="text-[10px] font-semibold uppercase tracking-wide text-zinc-500">Focus track</div>
              <div className="mt-1 max-w-[220px] truncate text-sm text-zinc-200">
                {selectedTrack?.title ?? preferredTrack?.title ?? "No active track"}
              </div>
            </div>
            <a href="/drop" className="btn-primary text-sm">Drop track</a>
          </div>
        </div>

        <div className="grid min-h-0 flex-1 grid-cols-1 grid-rows-1 gap-3 overflow-hidden xl:grid-cols-[280px,minmax(0,1fr)]">
          <aside className="flex min-h-0 flex-col overflow-hidden">
            <div className="workspace-panel flex min-h-0 flex-1 flex-col overflow-hidden">
              <div className="flex shrink-0 items-center justify-between gap-3 border-b border-surface-3 px-3 py-2.5">
                <div>
                  <h2 className="text-sm font-semibold uppercase tracking-wide text-zinc-400">Track rooms</h2>
                  <p className="text-[10px] text-zinc-600">Pick a room and read the table.</p>
                </div>
                <a href="/drop" className="text-[11px] font-semibold text-label-400 hover:text-label-300">Add</a>
              </div>
              <div className="hub-scroll min-h-0 flex-1 overflow-y-auto p-2">
                {tracksLoading ? (
                  <div className="space-y-2">
                    {[0, 1, 2].map((i) => (
                      <div key={i} className="h-24 rounded-xl border border-surface-3 bg-surface-2/40 animate-pulse" />
                    ))}
                  </div>
                ) : activeTracks.length === 0 ? (
                  <div className="flex h-full items-center justify-center rounded-xl border border-dashed border-surface-3 bg-surface-2/20 px-4 text-center">
                    <div>
                      <p className="text-sm text-zinc-500">No active tracks.</p>
                      <a href="/drop" className="mt-2 inline-block text-xs text-label-400 hover:underline">Drop one →</a>
                    </div>
                  </div>
                ) : (
                  <div className="space-y-2">
                    {sortedActiveTracks.map((track) => (
                      <TrackCard
                        key={track.id}
                        track={track}
                        selected={track.id === selectedTrack?.id}
                        onClick={() => setSelectedTrackId(track.id)}
                        messages={track.id === selectedTrack?.id ? messages : undefined}
                        onVault={() => void handleVault(track.id)}
                        onDelete={() => void handleDelete(track.id)}
                        onApprove={() => void handleApprove(track.id)}
                        railCompact
                      />
                    ))}
                  </div>
                )}
              </div>
            </div>
          </aside>

          <section className="flex min-h-0 flex-col overflow-hidden">
            <RoundtableReview
              track={selectedTrack}
              messages={messages}
              title={messagesLoading ? "Roundtable · syncing" : "Live roundtable"}
              onMessagesChanged={refreshMessages}
            />
          </section>
        </div>
      </div>

      {dragOver && (
        <div className="absolute inset-0 z-50 flex items-center justify-center bg-surface-0/90 pointer-events-none">
          <div className="text-center">
            <div className="text-5xl mb-4 select-none">🎵</div>
            <p className="text-xl font-semibold text-zinc-200">Drop to begin intake</p>
          </div>
        </div>
      )}
      {intakeState === "uploading" && (
        <div className="absolute inset-0 z-40 flex items-center justify-center bg-surface-0/80 backdrop-blur-sm pointer-events-none">
          <div className="text-center">
            <p className="text-sm text-zinc-400 mb-3 truncate max-w-xs">{intakeName}</p>
            <div className="w-64 bg-surface-2 rounded-full h-2 overflow-hidden">
              <div
                className="h-2 bg-label-500 rounded-full transition-all duration-300"
                style={{ width: `${intakeProgress}%` }}
              />
            </div>
            <p className="text-xs text-zinc-500 mt-2">{intakeProgress}%</p>
          </div>
        </div>
      )}
      {intakeState === "error" && (
        <div className="absolute bottom-6 left-1/2 -translate-x-1/2 z-40 rounded-lg border border-red-500/30 bg-red-900/20 px-4 py-2">
          <p className="text-xs text-red-300">
            Intake failed{intakeError ? ` — ${intakeError}` : "."}
          </p>
        </div>
      )}
    </div>
  );
}
