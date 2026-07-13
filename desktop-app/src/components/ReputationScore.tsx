interface Props {
  score: number;
  completionRate: number;
}

export default function ReputationScore({ score, completionRate }: Props) {
  const tier =
    score >= 200 ? "Gold" :
    score >= 100 ? "Silver" :
    score >= 50 ? "Bronze" : "Unsigned";

  const tierColor =
    score >= 200 ? "text-label-400" :
    score >= 100 ? "text-zinc-300" :
    score >= 50 ? "text-orange-400" : "text-zinc-500";

  return (
    <div className="card text-center">
      <div className={`text-3xl font-bold ${tierColor}`}>{score}</div>
      <div className="text-xs text-zinc-500 mt-1">label score</div>
      <div className={`text-sm font-semibold mt-2 ${tierColor}`}>{tier}</div>
      <div className="text-[10px] text-zinc-600 mt-1">
        {Math.round(completionRate)}% completion
      </div>
    </div>
  );
}
