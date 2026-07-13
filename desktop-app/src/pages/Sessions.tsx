import { useState } from "react";
import { useSessions, useExportEvents } from "../hooks/useHermesDB";
import type { AbletonSession } from "../lib/hermes-bridge";

type Filter = "all" | "week" | "month";

function formatDuration(minutes: number): string {
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

function formatTimeRange(start: string, end: string): string {
  const fmt = (s: string) =>
    new Date(s).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
  return `${fmt(start)} – ${fmt(end)}`;
}

function groupByProject(sessions: AbletonSession[]): Record<string, AbletonSession[]> {
  return sessions.reduce(
    (acc, s) => {
      (acc[s.project_name] ??= []).push(s);
      return acc;
    },
    {} as Record<string, AbletonSession[]>,
  );
}

function filterSessions(sessions: AbletonSession[], filter: Filter): AbletonSession[] {
  if (filter === "all") return sessions;
  const now = Date.now();
  const cutoff = filter === "week" ? 7 * 86400000 : 30 * 86400000;
  return sessions.filter((s) => now - new Date(s.started_at).getTime() < cutoff);
}

function SessionRow({ session }: { session: AbletonSession }) {
  const [open, setOpen] = useState(false);
  return (
    <div
      className="border-b border-surface-2 last:border-0 cursor-pointer"
      onClick={() => setOpen(!open)}
    >
      <div className="flex items-center gap-3 py-3 hover:bg-surface-2 px-3 rounded-lg transition-colors">
        {/* Date */}
        <div className="w-16 shrink-0 text-center">
          <p className="text-[10px] font-semibold text-zinc-400 uppercase tracking-wide">
            {new Date(session.session_date).toLocaleDateString("en-US", { month: "short", day: "numeric" })}
          </p>
        </div>

        {/* Time range + duration */}
        <div className="flex-1 min-w-0">
          <p className="text-sm text-zinc-300 font-medium">
            {formatTimeRange(session.started_at, session.ended_at)}
          </p>
          <p className="text-[10px] text-zinc-600 mt-0.5">
            {formatDuration(session.duration_minutes)}
          </p>
        </div>

        {/* Stats */}
        <div className="flex items-center gap-2 shrink-0 text-[10px]">
          <span
            className="bg-surface-2 text-zinc-400 px-2 py-0.5 rounded-md"
            title="saves"
          >
            {session.save_count} saves
          </span>
          <span
            className="bg-surface-2 text-zinc-400 px-2 py-0.5 rounded-md"
            title="exports"
          >
            {session.export_count} exports
          </span>
          {session.bpm !== null && (
            <span className="text-zinc-600">{session.bpm} bpm</span>
          )}
          {session.musical_key && (
            <span className="bg-label-500/10 text-label-500 px-2 py-0.5 rounded-md font-mono">
              {session.musical_key}
            </span>
          )}
        </div>

        {/* Expand arrow */}
        <span className={`text-zinc-600 text-xs transition-transform ${open ? "rotate-90" : ""}`}>
          ›
        </span>
      </div>

      {open && (
        <div className="px-4 pb-3 text-xs text-zinc-500 space-y-1 bg-surface-2/30 rounded-b-lg">
          {session.track_count !== null && (
            <p>{session.track_count} tracks in project</p>
          )}
          {session.bpm !== null && <p>BPM: {session.bpm}</p>}
          {session.musical_key && <p>Key: {session.musical_key}</p>}
        </div>
      )}
    </div>
  );
}

export default function Sessions() {
  const { sessions, loading } = useSessions();
  const { events } = useExportEvents();
  const [filter, setFilter] = useState<Filter>("all");

  const filtered = filterSessions(sessions, filter);
  const grouped = groupByProject(filtered);
  const projectNames = Object.keys(grouped).sort();

  const totalHours = sessions.reduce((s, r) => s + r.duration_minutes, 0) / 60;
  const changedExports = events.filter((e) => e.changed_from_prev === 1).length;

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-zinc-100">Sessions</h1>
        <div className="flex items-center gap-1 bg-surface-1 rounded-lg p-1">
          {(["all", "week", "month"] as Filter[]).map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`text-xs px-3 py-1.5 rounded-md capitalize transition-colors ${
                filter === f
                  ? "bg-label-500 text-black font-semibold"
                  : "text-zinc-500 hover:text-zinc-200"
              }`}
            >
              {f === "all" ? "All time" : f === "week" ? "This week" : "This month"}
            </button>
          ))}
        </div>
      </div>

      {/* Summary cards */}
      {!loading && sessions.length > 0 && (
        <div className="grid grid-cols-3 gap-4">
          <div className="card text-center">
            <p className="text-2xl font-bold text-zinc-200">{totalHours.toFixed(1)}h</p>
            <p className="text-xs text-zinc-600 mt-1">total tracked</p>
          </div>
          <div className="card text-center">
            <p className="text-2xl font-bold text-zinc-200">{sessions.length}</p>
            <p className="text-xs text-zinc-600 mt-1">sessions</p>
          </div>
          <div className="card text-center">
            <p className="text-2xl font-bold text-zinc-200">{changedExports}</p>
            <p className="text-xs text-zinc-600 mt-1">meaningful exports</p>
          </div>
        </div>
      )}

      {/* Sessions grouped by project */}
      {loading ? (
        <div className="space-y-4">
          {[0, 1, 2].map((i) => (
            <div key={i} className="card animate-pulse h-24" />
          ))}
        </div>
      ) : filtered.length === 0 ? (
        <div className="card text-center py-20">
          <p className="text-3xl mb-4">🎛️</p>
          <p className="text-zinc-400 font-medium mb-1">No sessions tracked yet</p>
          <p className="text-sm text-zinc-600 max-w-xs mx-auto">
            Point the app at your Ableton project folder in{" "}
            <a href="/settings" className="text-label-500 hover:underline">
              Settings
            </a>{" "}
            to start tracking your sessions automatically.
          </p>
        </div>
      ) : (
        <div className="space-y-6">
          {projectNames.map((project) => (
            <section key={project}>
              <div className="flex items-center gap-3 mb-2">
                <div className="w-2 h-2 bg-label-500 rounded-full" />
                <h2 className="text-sm font-semibold text-zinc-300">{project}</h2>
                <span className="text-[10px] text-zinc-600">
                  {grouped[project].length} sessions ·{" "}
                  {formatDuration(grouped[project].reduce((s, r) => s + r.duration_minutes, 0))} total
                </span>
              </div>
              <div className="card p-0 overflow-hidden">
                {grouped[project].map((s) => (
                  <SessionRow key={s.id} session={s} />
                ))}
              </div>
            </section>
          ))}
        </div>
      )}
    </div>
  );
}
