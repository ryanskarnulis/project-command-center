from __future__ import annotations

import enum
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, Index, String, func, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    """Timezone-aware UTC now — the app's single timestamp representation.

    ``func.now()`` writes naive strings on SQLite while the soft-delete/review
    stamps were already aware; mixing the two shapes made serialized JSON
    ambiguous (JS parses the naive form as local time). All Python-side writes go
    through this. ``server_default=func.now()`` stays on the columns purely so
    the DDL is unchanged (no migration); the ORM default always wins on insert.
    """
    return datetime.now(UTC)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        default=utcnow, onupdate=utcnow, server_default=func.now()
    )


class SoftDeleteMixin:
    deleted_at: Mapped[datetime | None] = mapped_column(default=None)


class InboxSource(enum.StrEnum):
    web = "web"
    discord = "discord"


class TaskReviewStatus(enum.StrEnum):
    candidate = "candidate"
    accepted = "accepted"
    rejected = "rejected"


class TaskWorkflowStatus(enum.StrEnum):
    open = "open"
    in_progress = "in_progress"
    done = "done"


class TaskPriority(enum.StrEnum):
    low = "low"
    medium = "medium"
    high = "high"
    urgent = "urgent"


class Project(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    description: Mapped[str | None] = mapped_column(default=None)
    system_key: Mapped[str | None] = mapped_column(default=None, unique=True)

    tasks: Mapped[list[Task]] = relationship(
        back_populates="project", foreign_keys="Task.project_id"
    )
    aliases: Mapped[list[ProjectAlias]] = relationship(back_populates="project")

    @property
    def is_protected(self) -> bool:
        return self.system_key is not None


class ProjectAlias(Base, TimestampMixin, SoftDeleteMixin):
    """An alternate name a project is referred to by in raw notes.

    The deterministic half of project matching: an extracted note's
    ``project_hint`` is matched against project names and these aliases in
    Python before any model is consulted.
    """

    __tablename__ = "project_aliases"
    __table_args__ = (
        # One active alias per (project, normalized form). Partial (active rows
        # only) so a soft-deleted alias never blocks re-adding the same text
        # later — mirrors the task-dependency active-edge index.
        Index(
            "uq_project_alias_normalized",
            "project_id",
            "normalized_alias",
            unique=True,
            sqlite_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    alias: Mapped[str]
    # Normalized form (services.projects._normalize: lowercased, trimmed,
    # internal whitespace collapsed) — the dedupe key, kept in lockstep with the
    # matcher so the guard and matching share semantics.
    normalized_alias: Mapped[str]

    project: Mapped[Project] = relationship(back_populates="aliases")


class Task(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id"), default=None
    )
    inbox_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("inbox_items.id"), default=None
    )
    # Self-referential nesting (Sprint 7 task-model slice). A null parent is a
    # top-level task; cycle prevention (no A->B->A) lives in services/tasks.py,
    # not the DB. Soft-deleting a parent cascade-soft-deletes its subtree.
    parent_task_id: Mapped[int | None] = mapped_column(
        ForeignKey("tasks.id"), default=None
    )
    title: Mapped[str]
    description: Mapped[str | None] = mapped_column(default=None)
    review_status: Mapped[TaskReviewStatus] = mapped_column(
        default=TaskReviewStatus.accepted
    )
    workflow_status: Mapped[TaskWorkflowStatus] = mapped_column(
        default=TaskWorkflowStatus.open
    )
    priority: Mapped[TaskPriority] = mapped_column(default=TaskPriority.medium)
    due_date: Mapped[date | None] = mapped_column(default=None)
    # Day-plan snooze (Today page "defer"). The scheduler skips this task while
    # deferred_until is after the plan's target date; nothing else reads it.
    # Null = not deferred.
    deferred_until: Mapped[date | None] = mapped_column(default=None)
    # Rough effort estimate (Sprint 7 task-model slice). Stored as whole minutes;
    # the UI maps it to human labels. Feeds future scheduling/kanban (not built).
    estimated_minutes: Mapped[int | None] = mapped_column(default=None)
    # Recurrence (Sprint 9L). ``repeat_interval`` is a JSON blob shaped
    # ``{"unit": "day"|"week"|"month", "every": 1-12}`` (null = non-recurring);
    # JSON avoids integer-drift on month math. ``recurrence_id`` is a shared
    # UUID chaining a series so "edit all future" and "skip" can target the
    # right rows without a join table. Both are pure persistence — the
    # next-occurrence logic lives in services/tasks.py.
    repeat_interval: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    recurrence_id: Mapped[str | None] = mapped_column(String(36), default=None)
    confidence: Mapped[float | None] = mapped_column(default=None)
    assignee_hint: Mapped[str | None] = mapped_column(default=None)
    # Raw "break this down" model output (Sprint 10a), held on the parent task
    # only between generating subtask candidates and reviewing them. The
    # correction (accepted/edited subtasks vs this original) is captured to
    # ai_training_examples at review time (prime directive #4); this column is
    # cleared once the breakdown is reviewed. Null = no pending breakdown.
    breakdown_output_json: Mapped[str | None] = mapped_column(default=None)
    # Set when a task is cascade-soft-deleted because its PROJECT was deleted
    # (services/projects.soft_delete_project). Lets restore_project bring back
    # exactly the set it removed — not tasks the user trashed independently.
    # Cleared when the task is restored. Null = trashed on its own (or active).
    deleted_with_project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id"), default=None
    )

    project: Mapped[Project | None] = relationship(
        back_populates="tasks", foreign_keys=[project_id]
    )
    inbox_item: Mapped[InboxItem | None] = relationship(back_populates="candidates")
    parent: Mapped[Task | None] = relationship(
        back_populates="subtasks", remote_side=[id]
    )
    subtasks: Mapped[list[Task]] = relationship(back_populates="parent")


class TaskDependency(Base, TimestampMixin, SoftDeleteMixin):
    """A 'must finish first' edge between tasks (Sprint 7 task-model slice).

    ``task_id depends_on depends_on_task_id`` means the depended-on task must be
    workflow-``done`` before this task can be started; a task is "blocked" (a
    derived state, computed in Python — there is no ``blocked`` status column)
    while any of its dependencies is unfinished. Cycle prevention (no A->B->A) lives in
    ``services/task_dependencies.py``, never in the DB. The partial unique index
    keeps one active edge per ordered pair while allowing a soft-deleted edge to be
    re-added later (mirrors the inbox ``input_hash`` index).
    """

    __tablename__ = "task_dependencies"
    __table_args__ = (
        Index(
            "uq_task_dependencies_active_edge",
            "task_id",
            "depends_on_task_id",
            unique=True,
            sqlite_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"))
    depends_on_task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"))


class InboxItem(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "inbox_items"
    __table_args__ = (
        Index(
            "uq_inbox_items_active_input_hash",
            "input_hash",
            unique=True,
            sqlite_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    raw_text: Mapped[str]
    input_hash: Mapped[str]
    source: Mapped[InboxSource] = mapped_column(default=InboxSource.web)
    summary: Mapped[str | None] = mapped_column(default=None)
    project_hint: Mapped[str | None] = mapped_column(default=None)
    needs_review: Mapped[bool] = mapped_column(default=True)
    processed_at: Mapped[datetime | None] = mapped_column(default=None)
    reviewed_at: Mapped[datetime | None] = mapped_column(default=None)
    model_output_json: Mapped[str | None] = mapped_column(default=None)
    model_name: Mapped[str | None] = mapped_column(default=None)

    # Project-matching suggestion (Sprint 4). ``suggested_project_id`` is the
    # project Python (alias lookup) or the model proposed for this note's tasks;
    # the match_* columns capture the model I/O when the AI fallback produced it,
    # so an override at review can be saved as a training example.
    suggested_project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id"), default=None
    )
    # The project alias that the deterministic matcher matched on, when the
    # suggestion came from an alias hit (not the project name, and not the AI
    # fallback). Surfaced at triage so the user sees *why* the note was routed.
    matched_alias: Mapped[str | None] = mapped_column(default=None)
    match_input_text: Mapped[str | None] = mapped_column(default=None)
    match_output_json: Mapped[str | None] = mapped_column(default=None)
    match_model_name: Mapped[str | None] = mapped_column(default=None)

    candidates: Mapped[list[Task]] = relationship(back_populates="inbox_item")


class ActivityEvent(Base, TimestampMixin):
    """Append-only audit log of project/task lifecycle changes (Sprint 6).

    Deliberately NOT soft-deletable (no ``deleted_at``): an audit trail is never
    user-edited. This is the one documented exception to the soft-delete rule in
    CLAUDE.md. ``created_at`` is the event time; ``project_id`` is indexed for the
    per-project activity feed query.
    """

    __tablename__ = "activity_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id"), index=True, default=None
    )
    entity_type: Mapped[str]  # "project" | "task"
    entity_id: Mapped[int]
    action: Mapped[str]  # "created" | "updated" | "completed" | "deleted"
    summary: Mapped[str]  # human-readable, e.g. 'Task "Fix VPN" created'


class EvalRun(Base, TimestampMixin):
    """Append-only history of eval-suite runs (Sprint 7).

    Like ``ActivityEvent``, this is deliberately NOT soft-deletable (no
    ``deleted_at``): it is a run log, never user-edited — the second documented
    exception to the soft-delete rule in CLAUDE.md. One row per suite run;
    ``created_at`` is the run time. Lets prompt/profile edits be judged as
    helping or regressing over time.
    """

    __tablename__ = "eval_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    suite: Mapped[str] = mapped_column(index=True)
    passed: Mapped[int]
    total: Mapped[int]


class AITrainingExample(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "ai_training_examples"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_name: Mapped[str]
    input_text: Mapped[str]
    model_output_json: Mapped[str]
    corrected_output_json: Mapped[str | None] = mapped_column(default=None)
    accepted: Mapped[bool] = mapped_column(default=False)
    model_profile: Mapped[str]
    model_name: Mapped[str]
