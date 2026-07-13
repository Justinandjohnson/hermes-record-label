"""Deal/project lifecycle and milestone tracking.

A "deal" represents a release project with well-defined milestones that must
be cleared by specific agents.  The deal board gives the Manager agent a
high-level view of all active projects.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Milestones
# ---------------------------------------------------------------------------

class MilestoneStage(str, enum.Enum):
    """Ordered milestones in a release deal."""

    DEMO_REVIEW = "demo_review"
    MIX_APPROVAL = "mix_approval"
    MASTER_DELIVERY = "master_delivery"
    ARTWORK = "artwork"
    RELEASE = "release"


class MilestoneStatus(str, enum.Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    BLOCKED = "blocked"


# Gate agents: which agent must clear each milestone.
MILESTONE_GATES: dict[MilestoneStage, str] = {
    MilestoneStage.DEMO_REVIEW: "a_and_r",
    MilestoneStage.MIX_APPROVAL: "a_and_r",
    MilestoneStage.MASTER_DELIVERY: "artist",
    MilestoneStage.ARTWORK: "creative_director",
    MilestoneStage.RELEASE: "manager",
}


class Milestone(BaseModel):
    """A single milestone within a deal."""

    stage: MilestoneStage
    status: MilestoneStatus = MilestoneStatus.PENDING
    gate_agent: str = ""
    completed_at: datetime | None = None
    completed_by: str | None = None
    notes: str = ""

    def model_post_init(self, __context: Any) -> None:
        if not self.gate_agent:
            self.gate_agent = MILESTONE_GATES.get(self.stage, "system")


# ---------------------------------------------------------------------------
# Deal
# ---------------------------------------------------------------------------

class DealStatus(str, enum.Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    ABANDONED = "abandoned"
    ON_HOLD = "on_hold"


class Deal(BaseModel):
    """A release project tracked on the deal board."""

    project_id: int
    track_id: int
    title: str = ""
    artist_name: str = ""
    status: DealStatus = DealStatus.ACTIVE
    milestones: list[Milestone] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    target_release_date: datetime | None = None
    actual_release_date: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        if not self.milestones:
            self.milestones = [Milestone(stage=stage) for stage in MilestoneStage]


# ---------------------------------------------------------------------------
# Deal Board
# ---------------------------------------------------------------------------

class DealBoard:
    """Manages the lifecycle of release deals and tracks milestone progress.

    The board is in-memory; persistence is handled by the caller via
    the snapshot/restore pattern.
    """

    def __init__(self) -> None:
        self._deals: dict[int, Deal] = {}  # keyed by project_id

    def create_deal(
        self,
        project_id: int,
        track_id: int,
        title: str = "",
        artist_name: str = "",
        target_release_date: datetime | None = None,
    ) -> Deal:
        """Create a new deal for a release project."""
        deal = Deal(
            project_id=project_id,
            track_id=track_id,
            title=title,
            artist_name=artist_name,
            target_release_date=target_release_date,
        )
        self._deals[project_id] = deal
        return deal

    def get_deal(self, project_id: int) -> Deal | None:
        return self._deals.get(project_id)

    def complete_milestone(
        self,
        project_id: int,
        stage: MilestoneStage,
        completed_by: str,
        notes: str = "",
    ) -> Milestone | None:
        """Mark a milestone as completed."""
        deal = self._deals.get(project_id)
        if deal is None:
            return None

        for ms in deal.milestones:
            if ms.stage == stage:
                ms.status = MilestoneStatus.COMPLETED
                ms.completed_at = datetime.utcnow()
                ms.completed_by = completed_by
                ms.notes = notes

                # Auto-advance next milestone to in_progress.
                self._advance_next(deal, stage)
                return ms

        return None

    def block_milestone(
        self,
        project_id: int,
        stage: MilestoneStage,
        reason: str = "",
    ) -> Milestone | None:
        """Mark a milestone as blocked."""
        deal = self._deals.get(project_id)
        if deal is None:
            return None

        for ms in deal.milestones:
            if ms.stage == stage:
                ms.status = MilestoneStatus.BLOCKED
                ms.notes = reason
                return ms
        return None

    def _advance_next(self, deal: Deal, completed_stage: MilestoneStage) -> None:
        """Set the next pending milestone to in_progress."""
        stages = list(MilestoneStage)
        idx = stages.index(completed_stage)
        if idx + 1 < len(stages):
            next_ms = deal.milestones[idx + 1]
            if next_ms.status == MilestoneStatus.PENDING:
                next_ms.status = MilestoneStatus.IN_PROGRESS

    def get_progress(self, project_id: int) -> dict[str, Any]:
        """Get milestone progress as a summary dict."""
        deal = self._deals.get(project_id)
        if deal is None:
            return {"error": "deal_not_found"}

        completed = sum(1 for ms in deal.milestones if ms.status == MilestoneStatus.COMPLETED)
        total = len(deal.milestones)

        return {
            "project_id": project_id,
            "title": deal.title,
            "status": deal.status.value,
            "completed": completed,
            "total": total,
            "progress_pct": round(completed / total * 100, 1) if total > 0 else 0.0,
            "current_stage": self._current_stage(deal),
            "milestones": [
                {
                    "stage": ms.stage.value,
                    "status": ms.status.value,
                    "gate_agent": ms.gate_agent,
                    "completed_at": ms.completed_at.isoformat() if ms.completed_at else None,
                }
                for ms in deal.milestones
            ],
        }

    def _current_stage(self, deal: Deal) -> str | None:
        """Return the stage currently in progress or the first pending one."""
        for ms in deal.milestones:
            if ms.status == MilestoneStatus.IN_PROGRESS:
                return ms.stage.value
        for ms in deal.milestones:
            if ms.status == MilestoneStatus.PENDING:
                return ms.stage.value
        return None

    def complete_deal(self, project_id: int) -> Deal | None:
        """Mark a deal as completed."""
        deal = self._deals.get(project_id)
        if deal is None:
            return None
        deal.status = DealStatus.COMPLETED
        deal.actual_release_date = datetime.utcnow()
        return deal

    def abandon_deal(self, project_id: int, reason: str = "") -> Deal | None:
        """Mark a deal as abandoned."""
        deal = self._deals.get(project_id)
        if deal is None:
            return None
        deal.status = DealStatus.ABANDONED
        deal.metadata["abandon_reason"] = reason
        return deal

    @property
    def active_deals(self) -> list[Deal]:
        return [d for d in self._deals.values() if d.status == DealStatus.ACTIVE]

    @property
    def all_deals(self) -> list[Deal]:
        return list(self._deals.values())
