import { useCallback, useEffect, useState } from "react";
import { actOnVerdict, getVerdict, synthesizeVerdict } from "../lib/hermes-bridge";
import type { Verdict } from "../lib/verdict";

/**
 * Tracks the active (non-superseded) verdict for a track. Polls every 5s so a
 * verdict produced by Dez asynchronously shows up without manual refresh.
 */
export function useVerdict(trackId: number | null) {
  const [verdict, setVerdict] = useState<Verdict | null>(null);
  const [loading, setLoading] = useState(true);
  const [synthesizing, setSynthesizing] = useState(false);
  const [acting, setActing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (trackId === null) {
      setVerdict(null);
      setLoading(false);
      return;
    }
    try {
      const v = await getVerdict(trackId);
      setVerdict(v);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [trackId]);

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 5000);
    return () => clearInterval(interval);
  }, [refresh]);

  const synthesize = useCallback(async () => {
    if (trackId === null) return null;
    setSynthesizing(true);
    setError(null);
    try {
      const v = await synthesizeVerdict(trackId);
      setVerdict(v);
      return v;
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      return null;
    } finally {
      setSynthesizing(false);
    }
  }, [trackId]);

  const act = useCallback(async () => {
    if (trackId === null || verdict === null) return null;
    setActing(true);
    setError(null);
    try {
      const result = await actOnVerdict(trackId, verdict.id);
      // After acting, the verdict is consumed — refresh to get the new state
      await refresh();
      return result;
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      return null;
    } finally {
      setActing(false);
    }
  }, [trackId, verdict, refresh]);

  return { verdict, loading, synthesizing, acting, error, refresh, synthesize, act };
}
