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
    # Closed = archived-but-visible-on-request: hidden from the board and
    # pickers, tasks untouched, reversible anytime (unlike the trash).
    closed_at: Mapped[datetime | None] = mapped_column(default=None)
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
    #   * ``deleted_at`` serves the active-task list and search
    #     (``deleted_at IS NULL``) plus the trash queries'
    #     ``deleted_at IS NOT NULL`` scan.
    #   * ``project_id``, ``parent_task_id``, ``recurrence_id`` each back a
    #     frequent equality filter (project scoping, subtree/children fetch,
    #     recurrence-series lookup) with no shared leading column, so each gets
    #     its own single-column index.
    # ``workflow_status`` is intentionally NOT indexed: it is never a SQL filter
    # (effective status is rolled up in Python), so an index on it would only add
    # write cost. Add one here if a SQL ``WHERE workflow_status`` ever lands.
    __table_args__ = (
        Index("ix_tasks_deleted_at", "deleted_at"),
        Index("ix_tasks_project_id", "project_id"),
        Index("ix_tasks_parent_task_id", "parent_task_id"),
        Index("ix_tasks_recurrence_id", "recurrence_id"),
        # A recurring series holds at most ONE live occurrence per due date. The
        # service layer guards this (task_recurrence.find_live_occurrence_on), but
        # the invariant is load-bearing enough to belong in the schema: the paths
        # that can breach it — a re-completion racing a skip, or restoring a
        # trashed occurrence whose date a replacement has taken — are exactly the
        # ones an application-level check gets wrong. Partial, so soft-deleted
        # history (the whole point of the trash) and non-recurring tasks are
        # exempt.
        Index(
            "uq_tasks_active_occurrence",
            "recurrence_id",
            "due_date",
            unique=True,
            sqlite_where=text("recurrence_id IS NOT NULL AND deleted_at IS NULL"),
            postgresql_where=text("recurrence_id IS NOT NULL AND deleted_at IS NULL"),
        ),
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
    # Set when a task is cascade-soft-deleted because its PROJECT was deleted
    # (services/projects.soft_delete_project). Lets restore_project bring back
    # exactly the set it removed — not tasks the user trashed independently.
    # Cleared when the task is restored. Null = trashed on its own (or active).
    deleted_with_project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id"), default=None
    )
    # Set when a task is cascade-soft-deleted because an ANCESTOR task was
    # trashed (services/tasks.soft_delete_task). Holds the id of the root the
    # user/agent actually trashed — flat, not one level up — so
    # ``task_trash.restore_task_subtree`` can bring back exactly the set that one
    # delete removed and leave descendants that were already in the trash
    # beforehand where they are. Cleared on any restore. Null = trashed on its
    # own (or active).
    #
    # Deliberately a plain Integer, not a ForeignKey: purging destroys rows, and
    # an FK would either block or cascade in ways the purge traversal (which is
    # project-scoped, see ``task_trash._deleted_subtree_depth_first``) doesn't
    # control. Standing in for the missing FK, ``task_trash.purge_task`` clears
    # this marker on every surviving row that names a row it destroys: ids are
    # plain rowids and get recycled, so a marker outliving its root would later
    # match an unrelated new task and pull foreign trash into that task's subtree
    # restore (issue #251).
    deleted_with_task_id: Mapped[int | None] = mapped_column(default=None)
    # Set when this occurrence was SKIPPED (services/task_recurrence.skip_occurrence)
    # rather than trashed normally. Both paths set ``deleted_at``; only this marker
    # tells them apart, and ``restore_task`` branches on it: a skip restores by
    # rolling the series back to this date, an ordinary delete restores in place.
    # Cleared on restore. Null = ordinary soft delete (or active).
    skipped_at: Mapped[datetime | None] = mapped_column(default=None)

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
    workflow-``done`` before this task can be completed; a task is "blocked" (a
    derived state, computed in Python — there is no ``blocked`` status column)
    while any of its dependencies is unfinished. Blocking gates completion, not
    starting: a blocked task may still be moved to ``in_progress``. Cycle prevention (no A->B->A) lives in
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


class ConversationRole(enum.StrEnum):
    user = "user"
    assistant = "assistant"


class Conversation(Base, TimestampMixin, SoftDeleteMixin):
    """One chat thread with the in-app agent (Phase 2 loop epic, slice 2).

    Soft delete is conversation-level only: messages are immutable children
    that ride along with their conversation (no per-message delete).
    ``updated_at`` is touched on every appended message so the conversation
    list can order by recency.
    """

    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Auto-derived from the first user message when not provided explicitly.
    title: Mapped[str | None] = mapped_column(default=None)

    messages: Mapped[list[ConversationMessage]] = relationship(
        back_populates="conversation", order_by="ConversationMessage.id"
    )


class ConversationMessage(Base, TimestampMixin):
    """One user or assistant turn in a conversation.

    The assistant turn persists the loop's outcome denormalized: ``content``
    is the reply text (null when the run stopped without one), ``tool_calls``
    is the list of dispatched tool calls with arguments and result/error
    (shape: ``app/ai/loop.py::ToolCallRecord``), ``stop_reason`` is the loop's
    termination cause. Stored here rather than recomputed from
    ``activity_events`` because the audit log records only mutations — reads
    (search, list_tasks) never land there — and carries neither arguments nor
    results; ``activity_events`` remains the audit source of truth for what
    changed. No ``deleted_at``: messages are immutable once written and share
    their conversation's soft-delete fate.
    """

    __tablename__ = "conversation_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id"), index=True
    )
    role: Mapped[ConversationRole]
    content: Mapped[str | None] = mapped_column(default=None)
    tool_calls: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, default=None)
    stop_reason: Mapped[str | None] = mapped_column(default=None)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


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
    # "created" | "updated" | "completed" | "deleted" (soft, restorable) |
    # "purged" (permanent). A "purged" row outlives the entity it names, so its
    # ``entity_id`` no longer resolves and its ``project_id`` is NULL when the
    # project itself was purged — the summary carries the name snapshot.
    action: Mapped[str]
    summary: Mapped[str]  # human-readable, e.g. 'Task "Fix VPN" created'
    # Who caused the event: NULL = the user (all pre-agent rows stay correct
    # without a backfill); agents stamp an identifier, e.g. "agent:mcp".
    actor: Mapped[str | None] = mapped_column(default=None)
