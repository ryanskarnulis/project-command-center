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
    # Manual board/sidebar position; ties broken by id so new projects
    # (sort_order 0 until first reorder) keep creation order.
    sort_order: Mapped[int] = mapped_column(default=0, server_default="0")

    tasks: Mapped[list[Task]] = relationship(
        back_populates="project", foreign_keys="Task.project_id"
    )

    @property
    def is_protected(self) -> bool:
        return self.system_key is not None


class Task(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "tasks"
    # Read-path indexes (Sprint 29 hardening). Profiled against the real service
    # queries, not the raw column list, to avoid dead write-overhead:
    #   * The active-task list and search both filter
    #     ``deleted_at IS NULL`` together with ``review_status`` — one compound
    #     index serves them, and its leading ``deleted_at`` column also covers
    #     the trash queries' ``deleted_at IS NOT NULL`` scan, so no standalone
    #     ``deleted_at``/``review_status`` index is needed.
    #   * ``project_id``, ``parent_task_id``, ``recurrence_id`` each back a
    #     frequent equality filter (project scoping, subtree/children fetch,
    #     recurrence-series lookup) with no shared leading column, so each gets
    #     its own single-column index.
    # ``workflow_status`` is intentionally NOT indexed: it is never a SQL filter
    # (effective status is rolled up in Python), so an index on it would only add
    # write cost. Add one here if a SQL ``WHERE workflow_status`` ever lands.
    __table_args__ = (
        Index("ix_tasks_deleted_at_review_status", "deleted_at", "review_status"),
        Index("ix_tasks_project_id", "project_id"),
        Index("ix_tasks_parent_task_id", "parent_task_id"),
        Index("ix_tasks_recurrence_id", "recurrence_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id"), default=None
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
    # Day-plan snooze (Focus page "defer"). The scheduler skips this task while
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
