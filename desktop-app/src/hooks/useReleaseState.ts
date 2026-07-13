import { useState, useEffect, useCallback } from "react";
import { getReleaseStates } from "../lib/hermes-bridge";
import type { ReleaseStateEntry } from "../lib/hermes-bridge";
import type { ReleaseState } from "../lib/state-machine";

export function useReleaseState(trackId: number | null) {
  const [history, setHistory] = useState<ReleaseStateEntry[]>([]);
  const [currentState, setCurrentState] = useState<ReleaseState>("DRAFT");
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    if (trackId === null) return;
    try {
      const states = await getReleaseStates(trackId);
      setHistory(states);
      if (states.length > 0) {
        setCurrentState(states[states.length - 1].to_state as ReleaseState);
      }
    } catch (err) {
      console.error("Failed to fetch release states:", err);
    } finally {
      setLoading(false);
    }
  }, [trackId]);

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 5000);
    return () => clearInterval(interval);
  }, [refresh]);

  return { history, currentState, loading, refresh };
}
