from __future__ import annotations

from collections.abc import Sequence

import structlog
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.ai import gateway
from app.ai.schemas import MatchInput, MatchOutput, ProjectChoice
from app.db.models import InboxItem, Project
from app.services import projects as projects_service
from app.services.inbox import list_candidates
from app.services.training_data import record_example

logger = structlog.get_logger(__name__)

_PROFILE = "project_matching"


def match_project_ai(
    *,
    project_hint: str,
    summary: str | None,
    task_titles: Sequence[str],
    choices: Sequence[ProjectChoice],
) -> tuple[MatchOutput | None, str, str]:
    """Call the matcher model and validate its output. No DB access.

    Returns ``(result, user_content, raw)``. ``result`` is the validated
    ``MatchOutput`` or ``None`` when the model returned invalid JSON or an id that
    was not offered (the Python guard — the model never invents a project).
    ``user_content`` (exactly what the model saw) and ``raw`` (exactly what it
    returned) come back too so the caller can persist them for training.
    """
    allowed = {choice.id for choice in choices}
    user_content = MatchInput(
        project_hint=project_hint,
        summary=summary,
        task_titles=list(task_titles),
        projects=list(choices),
    ).to_user_content()
    raw = gateway.complete(
        profile_name=_PROFILE,
        user_content=user_content,
        json_schema=MatchOutput.model_json_schema(),
    )

    try:
        result = MatchOutput.model_validate_json(raw)
    except ValidationError as exc:
        logger.error("match_validation_failed", raw_output=raw, error=str(exc))
        return None, user_content, raw

    if result.project_id is not None and result.project_id not in allowed:
        logger.warning(
            "match_id_not_offered",
            returned_id=result.project_id,
            offered=sorted(allowed),
        )
        return None, user_content, raw
    return result, user_content, raw


def match_inbox_item(db: Session, item: InboxItem) -> Project | None:
    """Resolve a processed inbox item to a suggested project (Sprint 4).

    Aliases first (pure Python); the model is consulted only on a miss, and its
    answer is guarded. The resulting ``suggested_project_id`` is stored on the
    item — the per-task choice is applied later, at review.

    Idempotent: once a suggestion (or an AI attempt) is recorded, a re-run does
    nothing and makes no model call. Non-fatal: a model validation failure is
    logged and saved as a training example, but the extracted tasks are kept and
    the item is simply left unmatched.
    """
    if item.reviewed_at is not None:
        return None
    # Already attempted? A deterministic hit set suggested_project_id; any AI
    # attempt (hit or miss) set match_output_json. Don't re-run either way.
    if item.suggested_project_id is not None:
        return projects_service.get_project(db, item.suggested_project_id)
    if item.match_output_json is not None:
        return None

    task_titles = [task.title for task in list_candidates(db, item.id)]

    # Step 1 — deterministic name/alias lookup over the whole note (hint, summary,
    # raw text, task titles). No model call. This is what catches a project named
    # by an alias even when the extractor produced no project_hint.
    search_text = " ".join(
        part
        for part in [item.project_hint, item.summary, item.raw_text, *task_titles]
        if part
    )
    match = projects_service.match_text_to_project_detailed(db, search_text)
    if match is not None:
        deterministic = match.project
        try:
            item.suggested_project_id = deterministic.id
            # Record which alias routed the note (None for a project-name hit) so
            # triage can show it; the AI fallback below leaves this unset.
            item.matched_alias = match.matched_alias
            db.commit()
            db.refresh(item)
        except Exception:
            db.rollback()
            raise
        logger.info(
            "match_deterministic",
            inbox_item_id=item.id,
            project_id=deterministic.id,
            matched_alias=match.matched_alias,
        )
        return deterministic

    # Step 2 — AI fallback (semantic match when no name/alias appears literally).
    pairs = projects_service.list_projects_with_aliases(db)
    signal = item.project_hint or item.summary
    if not pairs or (not signal and not task_titles):
        return None
    choices = [
        ProjectChoice(id=project.id, name=project.name, aliases=aliases)
        for project, aliases in pairs
    ]
    result, user_content, raw = match_project_ai(
        project_hint=signal or "",
        summary=item.summary,
        task_titles=task_titles,
        choices=choices,
    )

    model_name = gateway.get_profile(_PROFILE).model
    try:
        item.match_input_text = user_content
        item.match_output_json = raw
        item.match_model_name = model_name

        if result is None:
            # Invalid output or a non-offered id: record the raw output as a
            # failure case (prime directive #3) and leave the item unmatched.
            # The match_* fields and failure example commit together.
            record_example(
                db,
                task_name=_PROFILE,
                input_text=user_content,
                model_output_json=raw,
                model_profile=_PROFILE,
                model_name=model_name,
            )
            db.commit()
            db.refresh(item)
            logger.warning("match_unresolved", inbox_item_id=item.id)
            return None

        item.suggested_project_id = result.project_id
        db.commit()
        db.refresh(item)
    except Exception:
        db.rollback()
        raise

    logger.info(
        "match_ai",
        inbox_item_id=item.id,
        project_id=result.project_id,
        confidence=result.confidence,
    )
    if result.project_id is None:
        return None
    return projects_service.get_project(db, result.project_id)
