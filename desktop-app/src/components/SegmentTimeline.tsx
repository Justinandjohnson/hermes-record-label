import type { TrackSegment } from "../lib/hermes-bridge";

interface Props {
  segments: TrackSegment[];
  durationSec?: number | null;
}

/**
 * Thin strip showing the structural segmentation of a track. Each segment is
 * a colored block sized by its share of the track's duration. Standout
 * segments get a top marker dot. Hover reveals section label + visual anchor.
 *
 * No playback wiring yet — Phase 4 hooks click → seek when the player surface
 * exposes a `seek(seconds)` action.
 */
export default function SegmentTimeline({ segments, durationSec }: Props) {
  if (segments.length === 0) return null;

  // Total duration is either the prop or the last segment's end_sec
  const total = durationSec && durationSec > 0
    ? durationSec
    : segments[segments.length - 1].end_sec;
  if (total <= 0) return null;

  return (
    <div className="w-full">
      <div className="relative h-3 w-full overflow-hidden rounded-full border border-surface-3 bg-surface-2/40">
        {segments.map((seg) => {
          const left = (seg.start_sec / total) * 100;
          const width = ((seg.end_sec - seg.start_sec) / total) * 100;
          const energy = seg.energy ?? 5;
          // Brighter for higher energy, dimmer for lower
          const alpha = 0.15 + (energy / 10) * 0.45;
          const title = [
            seg.section_label,
            seg.mood ? `· ${seg.mood}` : null,
            seg.visual_anchor ? `\n${seg.visual_anchor}` : null,
            seg.standout && seg.standout_reason ? `\n★ ${seg.standout_reason}` : null,
          ]
            .filter(Boolean)
            .join(" ");
          return (
            <div
              key={seg.id}
              className="absolute top-0 h-full"
              style={{
                left: `${left}%`,
                width: `${width}%`,
                background: `rgba(132, 204, 22, ${alpha})`,
                borderRight: "1px solid rgba(0,0,0,0.25)",
              }}
              title={title}
            >
              {seg.standout && (
                <span
                  className="absolute -top-1 left-1/2 h-2 w-2 -translate-x-1/2 rounded-full bg-label-400 shadow-[0_0_4px_rgba(132,204,22,0.6)]"
                  aria-label="Standout moment"
                />
              )}
            </div>
          );
        })}
      </div>
      <div className="mt-1 flex justify-between text-[8px] text-zinc-600">
        <span>0:00</span>
        <span>{formatTime(total)}</span>
      </div>
    </div>
  );
}

function formatTime(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}
