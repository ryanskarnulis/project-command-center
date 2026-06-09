from __future__ import annotations

from datetime import date

import structlog

from app.ai import gateway
from app.ai.schemas import SummaryInput, SummaryTaskRow
from app.db.models import Task

logger = structlog.get_logger(__name__)

_PROFILE = "summary"


def summarize_project_ai(
    *,
    project_id: int,
    project_name: str,
    tasks: list[Task],
    today: date,
) -> str:
    """Call the summary workflow for one project and return plain-text prose.

    No DB writes — the caller passes data in. The ``summary`` profile uses
    ``response_mode: text`` so no Pydantic output schema is needed or applied.
    structlog carries ``project_id`` on every log line in this call's scope.
    """
    log = logger.bind(project_id=project_id)
    log.info("summary_started", task_count=len(tasks))

    task_rows = [
        SummaryTaskRow(
            title=t.title,
            workflow_status=t.workflow_status,
            priority=t.priority,
            due_date=t.due_date,
        )
        for t in tasks
    ]
    user_content = SummaryInput(
        project_name=project_name,
        tasks=task_rows,
        today=today,
    ).to_user_content()

    model_name = gateway.get_profile(_PROFILE).model
    text = gateway.complete(profile_name=_PROFILE, user_content=user_content)

    log.info("summary_completed", model=model_name, chars=len(text))
    return text
