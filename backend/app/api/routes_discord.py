from __future__ import annotations

import hmac

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.ai.workflows import extract_tasks as extract_workflow
from app.config import Settings, get_settings
from app.db.models import InboxSource
from app.db.session import get_db
from app.schemas.discord import DiscordInboxRequest, DiscordInboxResponse
from app.services import inbox as inbox_service

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/discord", tags=["discord"])


def require_shared_secret(
    settings: Settings = Depends(get_settings),
    x_backend_secret: str | None = Header(default=None),
) -> None:
    """Guard discord routes with a constant-time shared-secret header check.

    An empty `BACKEND_SHARED_SECRET` means the integration is not configured, so
    the route is disabled (503) rather than accepting anything. Otherwise the
    `X-Backend-Secret` header must match exactly — compared with
    `hmac.compare_digest` to avoid leaking the secret through timing. This is the
    real protection on the route (not the bind address), since the API may be
    exposed on the LAN.
    """
    if not settings.backend_shared_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="discord route not configured",
        )
    if x_backend_secret is None or not hmac.compare_digest(
        x_backend_secret, settings.backend_shared_secret
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid shared secret",
        )


@router.post(
    "/inbox",
    response_model=DiscordInboxResponse,
    dependencies=[Depends(require_shared_secret)],
)
def discord_inbox(
    data: DiscordInboxRequest, db: Session = Depends(get_db)
) -> DiscordInboxResponse:
    """Capture inbox text from Discord and run the shared extraction workflow.

    Create + extract in one call (idempotent on both steps), then return a
    summary the bot echoes back. Review happens in the web app — no accept/reject
    from Discord.
    """
    item = inbox_service.create_inbox_item(
        db, raw_text=data.raw_text, source=InboxSource.discord
    )
    try:
        candidates = extract_workflow.extract_tasks(db, item)
    except ValidationError:
        # The workflow already logged the raw output and wrote a failure training
        # row; surface the error rather than returning a silent empty list.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="extraction validation failed",
        ) from None

    logger.info(
        "discord_inbox_processed",
        inbox_item_id=item.id,
        candidate_count=len(candidates),
    )
    return DiscordInboxResponse(
        inbox_item_id=item.id,
        summary=item.summary,
        project_hint=item.project_hint,
        task_titles=[task.title for task in candidates],
        candidate_count=len(candidates),
        needs_review=item.needs_review,
    )
