import { useTracks } from "../hooks/useHermesDB";
import type { Track } from "../lib/hermes-bridge";
import { STATE_COLORS } from "../lib/state-machine";

function initials(title: string | null): string {
  if (!title) return "?";
  return title
    .split(/\s+/)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase() ?? "")
    .join("");
}

function formatDate(dateStr: string): string {
  return new Date(dateStr).toLocaleDateString("en-US", {
    month: "long",
    day: "numeric",
    year: "numeric",
  });
}

// Deterministic gradient based on track id
function artGradient(id: number): string {
  const gradients = [
    "from-purple-900/60 to-surface-2",
    "from-emerald-900/60 to-surface-2",
    "from-blue-900/60 to-surface-2",
    "from-orange-900/60 to-surface-2",
    "from-pink-900/60 to-surface-2",
    "from-cyan-900/60 to-surface-2",
    "from-yellow-900/60 to-surface-2",
    "from-red-900/60 to-surface-2",
  ];
  return gradients[id % gradients.length];
}

function ReleaseCard({ track }: { track: Track }) {
  return (
    <div className="card group hover:border-label-500/30 hover:scale-[1.02] transition-all duration-200 cursor-pointer p-0 overflow-hidden">
      {/* Art area */}
      <div
        className={`aspect-square bg-gradient-to-br ${artGradient(track.id)} flex items-center justify-center`}
      >
        <span className="text-4xl font-black text-white/20 select-none tracking-tighter group-hover:text-white/30 transition-colors">
          {initials(track.title)}
        </span>
      </div>

      {/* Info */}
      <div className="p-3">
        <h3 className="font-semibold text-sm text-zinc-200 truncate group-hover:text-label-400 transition-colors">
          {track.title ?? "Untitled"}
        </h3>
        <p className="text-[11px] text-zinc-500 mt-0.5">
          {formatDate(track.updated_at)}
        </p>
        <div className="flex items-center gap-2 mt-2">
          {track.format && (
            <span className="text-[10px] font-mono bg-surface-2 text-zinc-500 px-2 py-0.5 rounded-md uppercase">
              {track.format}
            </span>
          )}
          <span
            className={`text-[10px] font-mono font-semibold px-2 py-0.5 rounded-full uppercase tracking-wide text-white ${
              STATE_COLORS.RELEASED
            }`}
          >
            Released
          </span>
        </div>
      </div>
    </div>
  );
}

function EmptyState() {
  return (
    <div className="flex flex-col items-center justify-center py-32 text-center">
      {/* Abstract path illustration */}
      <svg
        width="120"
        height="80"
        viewBox="0 0 120 80"
        fill="none"
        className="mb-8 opacity-20"
      >
        <circle cx="20" cy="40" r="12" stroke="#f59e0b" strokeWidth="2" />
        <circle cx="60" cy="20" r="8" stroke="#f59e0b" strokeWidth="2" />
        <circle cx="100" cy="40" r="12" stroke="#f59e0b" strokeWidth="2" strokeDasharray="4 4" />
        <path d="M32 40 Q46 40 52 20" stroke="#3f3f46" strokeWidth="1.5" />
        <path d="M68 20 Q80 20 88 40" stroke="#3f3f46" strokeWidth="1.5" strokeDasharray="4 4" />
        <circle cx="60" cy="20" r="3" fill="#f59e0b" opacity="0.5" />
        <circle cx="20" cy="40" r="3" fill="#f59e0b" opacity="0.3" />
      </svg>
      <h2 className="text-lg font-semibold text-zinc-500 mb-2">No releases yet</h2>
      <p className="text-sm text-zinc-600 max-w-xs">
        Complete the pipeline — Draft → Review → Approved → Art → Release — and
        your tracks will appear here.
      </p>
      <a href="/deals" className="mt-6 btn-ghost text-sm">
        View Deal Board →
      </a>
    </div>
  );
}

function SkeletonGrid() {
  return (
    <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
      {[0, 1, 2, 3].map((i) => (
        <div key={i} className="card animate-pulse p-0 overflow-hidden">
          <div className="aspect-square bg-surface-2" />
          <div className="p-3 space-y-2">
            <div className="h-3.5 bg-surface-2 rounded-md w-3/4" />
            <div className="h-2.5 bg-surface-2 rounded-md w-1/2" />
          </div>
        </div>
      ))}
    </div>
  );
}

export default function ReleaseWall() {
  const { tracks, loading } = useTracks();
  const released = tracks.filter((t) => t.state === "RELEASED");

  return (
    <div className="p-6">
      <div className="flex items-baseline justify-between mb-6">
        <h1 className="text-2xl font-bold text-zinc-100">Releases</h1>
        {released.length > 0 && (
          <span className="text-sm text-zinc-500">
            {released.length} {released.length === 1 ? "release" : "releases"}
          </span>
        )}
      </div>

      {loading ? (
        <SkeletonGrid />
      ) : released.length === 0 ? (
        <EmptyState />
      ) : (
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
          {released.map((track) => (
            <ReleaseCard key={track.id} track={track} />
          ))}
        </div>
      )}
    </div>
  );
}
