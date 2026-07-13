import type { Project } from "../lib/hermes-bridge";

interface Milestone {
  name: string;
  state: "pending" | "active" | "cleared" | "skipped";
}

interface Props {
  project: Project;
  milestones?: Milestone[];
}

const DEFAULT_MILESTONES: Milestone[] = [
  { name: "Demo Review", state: "pending" },
  { name: "Mix Approval", state: "pending" },
  { name: "Master Delivery", state: "pending" },
  { name: "Artwork", state: "pending" },
  { name: "Release", state: "pending" },
];

export default function DealCard({ project, milestones = DEFAULT_MILESTONES }: Props) {
  const cleared = milestones.filter((m) => m.state === "cleared").length;
  const progress = milestones.length > 0 ? (cleared / milestones.length) * 100 : 0;

  return (
    <div className="card">
      <div className="flex items-start justify-between mb-3">
        <div>
          <h3 className="font-semibold text-zinc-100">{project.title}</h3>
          <span className="text-xs text-zinc-500 uppercase">{project.type}</span>
        </div>
        {project.target_release_date && (
          <span className="text-xs text-zinc-400 bg-surface-2 px-2 py-1 rounded">
            {project.target_release_date}
          </span>
        )}
      </div>

      <div className="w-full bg-surface-2 rounded-full h-1.5 mb-3">
        <div
          className="bg-label-500 h-1.5 rounded-full transition-all"
          style={{ width: `${progress}%` }}
        />
      </div>

      <div className="flex gap-1.5 flex-wrap">
        {milestones.map((m) => (
          <span
            key={m.name}
            className={`text-[10px] font-mono px-2 py-0.5 rounded ${
              m.state === "cleared"
                ? "bg-emerald-900/40 text-emerald-400"
                : m.state === "active"
                  ? "bg-label-900/40 text-label-400"
                  : m.state === "skipped"
                    ? "bg-zinc-800 text-zinc-600 line-through"
                    : "bg-surface-2 text-zinc-500"
            }`}
          >
            {m.name}
          </span>
        ))}
      </div>
    </div>
  );
}
