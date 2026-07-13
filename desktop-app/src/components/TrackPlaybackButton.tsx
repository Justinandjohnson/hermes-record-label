import { useEffect, useState } from "react";
import type { MouseEvent } from "react";
import { subscribePlayback, toggleTrackPlayback } from "../lib/track-playback";

interface Props {
  trackId: number;
  compact?: boolean;
}

export default function TrackPlaybackButton({ trackId, compact = false }: Props) {
  const [playing, setPlaying] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    return subscribePlayback((snapshot) => {
      setPlaying(snapshot.trackId === trackId && snapshot.playing);
      setLoading(snapshot.trackId === trackId && snapshot.loading);
      setError(snapshot.trackId === trackId ? snapshot.error : null);
    });
  }, [trackId]);

  const handleClick = async (event: MouseEvent<HTMLButtonElement>) => {
    event.preventDefault();
    event.stopPropagation();
    await toggleTrackPlayback(trackId);
  };

  const label = loading ? "Loading" : playing ? "Pause" : "Play";

  return (
    <button
      type="button"
      className={`btn-ghost shrink-0 ${compact ? "px-2 py-1 text-[11px]" : "px-3 py-1.5 text-xs"}`}
      onClick={(event) => void handleClick(event)}
      title={error ?? `${label} track`}
      aria-label={`${label} track`}
    >
      {label}
    </button>
  );
}
