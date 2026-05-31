from __future__ import annotations

import enum
from datetime import date, datetime

from sqlalchemy import ForeignKey, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now()
    )


class SoftDeleteMixin:
    deleted_at: Mapped[datetime | None] = mapped_column(default=None)


class InboxSource(enum.StrEnum):
    web = "web"
    discord = "discord"


class TaskStatus(enum.StrEnum):
    candidate = "candidate"
    accepted = "accepted"
    rejected = "rejected"
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

    tasks: Mapped[list[Task]] = relationship(back_populates="project")


class Task(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id"), default=None
    )
    inbox_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("inbox_items.id"), default=None
    )
    title: Mapped[str]
    description: Mapped[str | None] = mapped_column(default=None)
    status: Mapped[TaskStatus] = mapped_column(default=TaskStatus.accepted)
    priority: Mapped[TaskPriority] = mapped_column(default=TaskPriority.medium)
    due_date: Mapped[date | None] = mapped_column(default=None)
    confidence: Mapped[float | None] = mapped_column(default=None)
    assignee_hint: Mapped[str | None] = mapped_column(default=None)

    project: Mapped[Project | None] = relationship(back_populates="tasks")
    inbox_item: Mapped[InboxItem | None] = relationship(back_populates="candidates")


class InboxItem(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "inbox_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    raw_text: Mapped[str]
    input_hash: Mapped[str] = mapped_column(index=True)
    source: Mapped[InboxSource] = mapped_column(default=InboxSource.web)
    summary: Mapped[str | None] = mapped_column(default=None)
    project_hint: Mapped[str | None] = mapped_column(default=None)
    needs_review: Mapped[bool] = mapped_column(default=True)
    processed_at: Mapped[datetime | None] = mapped_column(default=None)
    model_output_json: Mapped[str | None] = mapped_column(default=None)
    model_name: Mapped[str | None] = mapped_column(default=None)

    candidates: Mapped[list[Task]] = relationship(back_populates="inbox_item")


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
