import { useEffect, useState } from "react";
import {
  generateArtwork,
  getArtworkGenerations,
  getRemoteConfig,
  pickArtwork,
} from "../lib/hermes-bridge";
import type { ArtworkGeneration } from "../lib/hermes-bridge";

interface Props {
  trackId: number;
  trackTitle: string;
}

/**
 * Shows Maren's cover-art variants for a track. Polls every 10s while no
 * generations exist (Maren may be running in the background). Once images
 * land, displays a 2×2 grid with rationale + a Pick button per variant.
 */
export default function ArtworkVariants({ trackId, trackTitle }: Props) {
  const [generations, setGenerations] = useState<ArtworkGeneration[]>([]);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [picking, setPicking] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const remote = getRemoteConfig();

  const refresh = async () => {
    try {
      const data = await getArtworkGenerations(trackId);
      setGenerations(data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void refresh();
    const interval = setInterval(refresh, 10_000);
    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [trackId]);

  const handleGenerate = async () => {
    setGenerating(true);
    setError(null);
    try {
      const created = await generateArtwork(trackId);
      setGenerations(created);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setGenerating(false);
    }
  };

  const handlePick = async (generationId: number) => {
    setPicking(generationId);
    try {
      await pickArtwork(generationId);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setPicking(null);
    }
  };

  const imageUrl = (gen: ArtworkGeneration): string | null => {
    if (!gen.image_url || !remote) return null;
    return `${remote.url}/artwork/image?generation_id=${gen.id}`;
  };

  if (loading) {
    return (
      <div className="rounded-xl border border-surface-3 bg-surface-1/50 p-3 text-center">
        <p className="text-xs text-zinc-600">Loading cover variants…</p>
      </div>
    );
  }

  if (generations.length === 0) {
    return (
      <div className="rounded-xl border border-surface-3 bg-surface-1/50 p-4 text-center">
        <p className="text-xs text-zinc-400">
          Maren hasn't sent cover variants yet for <em>{trackTitle}</em>.
        </p>
        <button
          type="button"
          disabled={generating}
          onClick={() => void handleGenerate()}
          className="btn-primary mt-3 text-xs disabled:opacity-50"
        >
          {generating ? "Maren is working…" : "Generate cover variants"}
        </button>
        {error && <p className="mt-2 text-[10px] text-red-400">{error}</p>}
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-surface-3 bg-surface-1/50 p-3">
      <div className="mb-2 flex items-center justify-between gap-2">
        <p className="text-xs font-semibold text-zinc-200">
          Cover variants — &quot;{trackTitle}&quot;
        </p>
        <button
          type="button"
          disabled={generating}
          onClick={() => void handleGenerate()}
          className="text-[10px] font-semibold text-zinc-500 hover:text-zinc-300 disabled:opacity-50"
        >
          {generating ? "Generating…" : "Regenerate"}
        </button>
      </div>
      {error && <p className="mb-2 text-[10px] text-red-400">{error}</p>}
      <div className="grid grid-cols-2 gap-2">
        {generations.map((gen) => {
          const url = imageUrl(gen);
          const isPicked = gen.picked === 1;
          return (
            <div
              key={gen.id}
              className={`rounded-lg border p-2 ${
                isPicked
                  ? "border-label-500/60 bg-label-500/10"
                  : "border-surface-3 bg-surface-2/40"
              }`}
            >
              <div className="aspect-square overflow-hidden rounded bg-surface-0">
                {url ? (
                  <img
                    src={url}
                    alt={gen.rationale ?? "Cover variant"}
                    className="h-full w-full object-cover"
                  />
                ) : (
                  <div className="flex h-full items-center justify-center px-2 text-center">
                    <p className="text-[10px] text-zinc-600">
                      Generation failed for this variant
                    </p>
                  </div>
                )}
              </div>
              {gen.variant_axis && (
                <p className="mt-1.5 text-[9px] uppercase tracking-wide text-zinc-500">
                  Axis: {gen.variant_axis}
                </p>
              )}
              {gen.rationale && (
                <p className="mt-1 text-[10px] leading-snug text-zinc-300 line-clamp-3">
                  {gen.rationale}
                </p>
              )}
              {url && (
                <button
                  type="button"
                  disabled={picking === gen.id || isPicked}
                  onClick={() => void handlePick(gen.id)}
                  className={`mt-1.5 w-full rounded-md px-2 py-1 text-[10px] font-semibold transition-colors disabled:opacity-50 ${
                    isPicked
                      ? "bg-label-500 text-black"
                      : "border border-surface-3 bg-surface-1 text-zinc-200 hover:bg-surface-2"
                  }`}
                >
                  {isPicked
                    ? "Picked"
                    : picking === gen.id
                      ? "Picking…"
                      : "Pick this one"}
                </button>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
