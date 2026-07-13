interface Props {
  currentStreak: number;
  longestStreak: number;
}

export default function StreakBadge({ currentStreak, longestStreak }: Props) {
  const isHot = currentStreak >= 3;
  const isOnFire = currentStreak >= 5;

  return (
    <div className={`card text-center ${isOnFire ? "border-label-500/50" : ""}`}>
      <div className={`text-3xl font-bold ${isHot ? "text-label-400" : "text-zinc-300"}`}>
        {currentStreak}
      </div>
      <div className="text-xs text-zinc-500 mt-1">
        week streak {isOnFire ? " (on fire)" : isHot ? " (hot)" : ""}
      </div>
      <div className="text-[10px] text-zinc-600 mt-2">
        best: {longestStreak}w
      </div>
    </div>
  );
}
