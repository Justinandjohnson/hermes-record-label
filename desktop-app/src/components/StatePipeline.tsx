import { RELEASE_STATES, STATE_LABELS, STATE_COLORS, stateIndex } from "../lib/state-machine";
import type { ReleaseState } from "../lib/state-machine";

interface Props {
  currentState: ReleaseState;
}

export default function StatePipeline({ currentState }: Props) {
  const currentIdx = stateIndex(currentState);

  return (
    <div className="flex items-center gap-1 overflow-x-auto pb-1">
      {RELEASE_STATES.map((state, idx) => {
        const isPast = idx < currentIdx;
        const isCurrent = idx === currentIdx;

        return (
          <div key={state} className="flex items-center gap-1 shrink-0">
            <div
              className={`
                text-[10px] font-mono px-2 py-1 rounded-md transition-all
                ${isCurrent ? `${STATE_COLORS[state]} text-white font-bold step-active` : ""}
                ${isPast ? "bg-surface-2 text-zinc-400" : ""}
                ${!isPast && !isCurrent ? "bg-surface-1 text-zinc-600" : ""}
              `}
            >
              {STATE_LABELS[state]}
            </div>
            {idx < RELEASE_STATES.length - 1 && (
              <span className={`text-xs ${isPast ? "text-zinc-500" : "text-zinc-700"}`}>
                &rarr;
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}
