import { useEffect, useState } from "react";
import type { MouseEvent } from "react";
import { subscribeVoicePlayback, toggleVoicePlayback } from "../lib/voice-playback";

interface Props {
  messageId: number;
}

export default function VoicePlayButton({ messageId }: Props) {
  const [playing, setPlaying] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    return subscribeVoicePlayback((snapshot) => {
      setPlaying(snapshot.messageId === messageId && snapshot.playing);
      setLoading(snapshot.messageId === messageId && snapshot.loading);
      setError(snapshot.messageId === messageId ? snapshot.error : null);
    });
  }, [messageId]);

  const handleClick = async (event: MouseEvent<HTMLButtonElement>) => {
    event.preventDefault();
    event.stopPropagation();
    await toggleVoicePlayback(messageId);
  };

  const label = loading ? "Loading voice" : playing ? "Pause voice" : "Play voice";

  return (
    <button
      type="button"
      onClick={(event) => void handleClick(event)}
      title={error ?? label}
      aria-label={label}
      className="shrink-0 text-[10px] text-zinc-500 hover:text-zinc-200 transition-colors disabled:opacity-40"
      disabled={loading}
    >
      {loading ? "…" : playing ? "⏸" : "🔊"}
    </button>
  );
}
