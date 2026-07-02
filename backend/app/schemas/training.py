from __future__ import annotations


from pydantic import BaseModel, ConfigDict, computed_field

from app.schemas.common import UTCDateTime

# When the corpus reaches this many rows, custom-model fine-tuning (Sprint 8)
# becomes viable. The progress meter counts toward this goal.
FINE_TUNE_GOAL = 200


class TrainingExampleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    task_name: str
    input_text: str
    model_output_json: str
    corrected_output_json: str | None
    accepted: bool
    model_profile: str
    model_name: str
    created_at: UTCDateTime
    deleted_at: UTCDateTime | None


class TaskStat(BaseModel):
    """Per-task corpus counts: total rows and how many were accepted."""

    count: int
    accepted: int


class TrainingStatsRead(BaseModel):
    total: int
    accepted: int
    by_task: dict[str, TaskStat]
    profiles: list[str] = []
    goal: int = FINE_TUNE_GOAL

    @computed_field  # type: ignore[prop-decorator]
    @property
    def remaining(self) -> int:
        """Rows still needed before fine-tuning is viable (never negative)."""
        return max(self.goal - self.total, 0)
