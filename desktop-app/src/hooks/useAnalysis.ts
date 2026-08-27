import { useEffect, useState } from "react";
import type { TrackAnalysisDetails } from "../lib/hermes-bridge";
import { getAnalysis } from "../lib/hermes-bridge";

export function useAnalysis(trackId: number | null) {
  const [analysis, setAnalysis] = useState<TrackAnalysisDetails | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (trackId === null) {
      setAnalysis(null);
      return;
    }
    let cancelled = false;
    setLoading(true);

    getAnalysis(trackId)
      .then((res) => {
        if (!cancelled) {
          setAnalysis(res.analysis);
        }
      })
      .catch(() => {
        if (!cancelled) setAnalysis(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [trackId]);

  return { analysis, loading };
}
