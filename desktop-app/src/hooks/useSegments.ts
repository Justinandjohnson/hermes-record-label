import { useCallback, useEffect, useState } from "react";
import { getSegments } from "../lib/hermes-bridge";
import type { TrackSegment } from "../lib/hermes-bridge";

/**
 * Fetches granular segments for a track. Polls every 10s so that segments
 * produced by the dispatcher's second-pass analysis show up without manual
 * refresh.
 */
export function useSegments(trackId: number | null) {
  const [segments, setSegments] = useState<TrackSegment[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (trackId === null) {
      setSegments([]);
      setLoading(false);
      return;
    }
    try {
      const segs = await getSegments(trackId);
      setSegments(segs);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [trackId]);

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 10_000);
    return () => clearInterval(interval);
  }, [refresh]);

  return { segments, loading, error, refresh };
}
