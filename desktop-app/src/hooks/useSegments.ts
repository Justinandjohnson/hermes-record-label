import { useCallback, useEffect, useRef, useState } from "react";
import { getSegments } from "../lib/hermes-bridge";
import type { TrackSegment } from "../lib/hermes-bridge";

const ACTIVE_POLL_MS = 5_000;
/** Once segments exist we only need to catch late second-pass additions. */
const SETTLED_POLL_MS = 30_000;

/**
 * Fetches granular segments for a track. Polls quickly until segments appear,
 * then backs off so that segments produced by the dispatcher's second-pass
 * analysis still show up without hammering the API forever.
 */
export function useSegments(trackId: number | null) {
  const [segments, setSegments] = useState<TrackSegment[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const hasDataRef = useRef(false);

  const refresh = useCallback(async () => {
    if (trackId === null) {
      setSegments([]);
      setLoading(false);
      return;
    }
    try {
      const segs = await getSegments(trackId);
      hasDataRef.current = segs.length > 0;
      setSegments(segs);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [trackId]);

  useEffect(() => {
    let cancelled = false;
    let handle: ReturnType<typeof setTimeout>;
    const tick = () => {
      void refresh().then(() => {
        if (cancelled) return;
        handle = setTimeout(tick, hasDataRef.current ? SETTLED_POLL_MS : ACTIVE_POLL_MS);
      });
    };
    tick();
    return () => {
      cancelled = true;
      clearTimeout(handle);
    };
  }, [refresh]);

  return { segments, loading, error, refresh };
}
