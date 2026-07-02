from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import cast

import pytest

from app.ai import gateway
from app.ai.workflows.summarize_project import summarize_project_ai
from app.db.models import Task, TaskPriority, TaskWorkflowStatus


def _make_task(
    title: str,
    workflow_status: TaskWorkflowStatus = TaskWorkflowStatus.open,
) -> Task:
    """Minimal task-like object for the summary workflow (duck-typed, cast)."""
    return cast(Task, SimpleNamespace(
        title=title,
        workflow_status=workflow_status,
        priority=TaskPriority.medium,
        due_date=None,
    ))


def test_summarize_project_calls_gateway_and_returns_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_complete(**kwargs: object) -> str:
        captured.update(kwargs)
        return "This project has one open task."

    monkeypatch.setattr(gateway, "complete", fake_complete)

    task = _make_task("Fix the router config")
    result = summarize_project_ai(
        project_id=42,
        project_name="Firewall",
        tasks=[task],
        today=date(2026, 5, 31),
    )

    assert result == "This project has one open task."
    assert captured.get("profile_name") == "summary"
    user_content = captured.get("user_content", "")
    assert isinstance(user_content, str)
    assert "Firewall" in user_content
    assert "Fix the router config" in user_content


def test_summarize_project_no_tasks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gateway, "complete", lambda **_: "No open tasks.")

    result = summarize_project_ai(
        project_id=1,
        project_name="Empty",
        tasks=[],
        today=date(2026, 5, 31),
    )
    assert result == "No open tasks."
