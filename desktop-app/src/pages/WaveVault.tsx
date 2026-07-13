import { useEffect, useMemo, useState } from "react";
import { getWaveVault } from "../lib/hermes-bridge";
import type { WaveVaultEntry } from "../lib/hermes-bridge";

const STEM_LABELS: Record<string, string> = {
  vocals: "Vocals",
  drums: "Drums",
  bass: "Bass",
  other: "Instrumental",
  full: "Full mix",
};

const STEM_COLORS: Record<string, string> = {
  vocals: "border-purple-500/40 bg-purple-500/10 text-purple-300",
  drums: "border-rose-500/40 bg-rose-500/10 text-rose-300",
  bass: "border-amber-500/40 bg-amber-500/10 text-amber-300",
  other: "border-cyan-500/40 bg-cyan-500/10 text-cyan-300",
  full: "border-emerald-500/40 bg-emerald-500/10 text-emerald-300",
};

function formatRange(start: number | null, end: number | null): string {
  if (start === null || end === null) return "whole stem";
  const fmt = (s: number) => {
    const m = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    return `${m}:${sec.toString().padStart(2, "0")}`;
  };
  return `${fmt(start)} – ${fmt(end)}`;
}

export default function WaveVault() {
  const [entries, setEntries] = useState<WaveVaultEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [stemFilter, setStemFilter] = useState<string | "all">("all");
  const [bpmFilter, setBpmFilter] = useState<string>("");
  const [keyFilter, setKeyFilter] = useState<string>("");

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const data = await getWaveVault();
        if (!cancelled) {
          setEntries(data);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : String(err));
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void load();
    const interval = setInterval(load, 10_000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  const filtered = useMemo(() => {
    return entries.filter((e) => {
      if (stemFilter !== "all" && e.stem !== stemFilter) return false;
      if (bpmFilter) {
        const wanted = parseFloat(bpmFilter);
        if (!isNaN(wanted) && (e.bpm === null || Math.abs(e.bpm - wanted) > 3)) {
          return false;
        }
      }
      if (keyFilter && e.musical_key) {
        if (!e.musical_key.toLowerCase().includes(keyFilter.toLowerCase())) {
          return false;
        }
      } else if (keyFilter && !e.musical_key) {
        return false;
      }
      return true;
    });
  }, [entries, stemFilter, bpmFilter, keyFilter]);

  return (
    <div className="flex h-full min-h-0 w-full flex-col overflow-hidden bg-surface-0">
      <div className="mx-auto flex h-full min-h-0 w-full max-w-[1800px] flex-col overflow-hidden px-3 py-3 lg:px-4">
        <div className="mb-3 flex shrink-0 items-center justify-between gap-4 border-b border-surface-3 pb-3">
          <div className="min-w-0">
            <div className="text-[11px] font-semibold uppercase tracking-[0.22em] text-zinc-500">
              Library
            </div>
            <h1 className="mt-1 text-lg font-semibold text-zinc-100">Wave Vault</h1>
            <p className="mt-1 text-xs text-zinc-500">
              Loops and stems pulled from tracks the roundtable chose to mine
              instead of ship.
            </p>
          </div>
          <div className="text-right">
            <div className="text-[10px] uppercase tracking-wide text-zinc-500">In vault</div>
            <div className="text-2xl font-semibold text-zinc-100">{entries.length}</div>
          </div>
        </div>

        {/* Filters */}
        <div className="mb-3 flex shrink-0 items-center gap-2">
          <select
            value={stemFilter}
            onChange={(e) => setStemFilter(e.target.value)}
            className="rounded-lg border border-surface-3 bg-surface-1 px-2 py-1 text-xs text-zinc-200"
          >
            <option value="all">All stems</option>
            <option value="vocals">Vocals</option>
            <option value="drums">Drums</option>
            <option value="bass">Bass</option>
            <option value="other">Instrumental</option>
            <option value="full">Full mix</option>
          </select>
          <input
            type="number"
            placeholder="BPM"
            value={bpmFilter}
            onChange={(e) => setBpmFilter(e.target.value)}
            className="w-20 rounded-lg border border-surface-3 bg-surface-1 px-2 py-1 text-xs text-zinc-200 placeholder:text-zinc-600"
          />
          <input
            type="text"
            placeholder="Key (e.g. G minor)"
            value={keyFilter}
            onChange={(e) => setKeyFilter(e.target.value)}
            className="w-36 rounded-lg border border-surface-3 bg-surface-1 px-2 py-1 text-xs text-zinc-200 placeholder:text-zinc-600"
          />
          {(stemFilter !== "all" || bpmFilter || keyFilter) && (
            <button
              type="button"
              onClick={() => {
                setStemFilter("all");
                setBpmFilter("");
                setKeyFilter("");
              }}
              className="text-[11px] text-zinc-500 hover:text-zinc-300"
            >
              Clear filters
            </button>
          )}
        </div>

        {/* Body */}
        <div className="min-h-0 flex-1 overflow-y-auto">
          {loading ? (
            <div className="flex h-full items-center justify-center">
              <p className="text-xs text-zinc-600">Loading…</p>
            </div>
          ) : error ? (
            <div className="flex h-full items-center justify-center">
              <p className="text-xs text-red-400">{error}</p>
            </div>
          ) : entries.length === 0 ? (
            <div className="flex h-full items-center justify-center">
              <div className="max-w-md text-center">
                <div className="mb-3 text-4xl">🌊</div>
                <h2 className="text-sm font-semibold text-zinc-200">
                  Nothing in the vault yet
                </h2>
                <p className="mt-2 text-xs leading-relaxed text-zinc-500">
                  When the roundtable lands on a verdict of <em>mine for loops</em>,
                  Rubin or Kallman pulls the moment that's working and drops it
                  here. Stems get tagged with BPM and key, so you can pull them
                  back into a new track later.
                </p>
              </div>
            </div>
          ) : filtered.length === 0 ? (
            <div className="flex h-full items-center justify-center">
              <p className="text-xs text-zinc-600">No matches for the current filters.</p>
            </div>
          ) : (
            <div className="grid grid-cols-1 gap-2 md:grid-cols-2 xl:grid-cols-3">
              {filtered.map((entry) => (
                <div
                  key={entry.id}
                  className="rounded-xl border border-surface-3 bg-surface-1/80 p-3"
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <p className="truncate text-sm font-semibold text-zinc-100">
                        {entry.track_title ?? `Track #${entry.track_id}`}
                      </p>
                      <p className="mt-0.5 text-[10px] uppercase tracking-wide text-zinc-500">
                        {formatRange(entry.start_sec, entry.end_sec)}
                      </p>
                    </div>
                    <span
                      className={`shrink-0 rounded-full border px-2 py-0.5 text-[10px] font-semibold ${
                        STEM_COLORS[entry.stem] ??
                        "border-surface-3 bg-surface-2 text-zinc-400"
                      }`}
                    >
                      {STEM_LABELS[entry.stem] ?? entry.stem}
                    </span>
                  </div>
                  {entry.notes && (
                    <p className="mt-2 text-xs leading-relaxed text-zinc-400">
                      {entry.notes}
                    </p>
                  )}
                  <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[10px] text-zinc-500">
                    {entry.bpm !== null && (
                      <span className="rounded-full bg-surface-2 px-2 py-0.5">
                        {entry.bpm} BPM
                      </span>
                    )}
                    {entry.musical_key && (
                      <span className="rounded-full bg-surface-2 px-2 py-0.5">
                        {entry.musical_key}
                      </span>
                    )}
                    {entry.tags.map((tag) => (
                      <span
                        key={tag}
                        className="rounded-full bg-surface-2 px-2 py-0.5"
                      >
                        {tag}
                      </span>
                    ))}
                    {entry.added_by && (
                      <span className="ml-auto text-zinc-600">
                        via {entry.added_by}
                      </span>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
