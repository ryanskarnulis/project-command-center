from __future__ import annotations

from collections.abc import Sequence
from ipaddress import ip_address

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.db.models import EvalRun
from app.db.session import get_db
from app.schemas.settings import (
    EvalRunRecord,
    EvalRunResult,
    ProfileRead,
    ProfileUpdate,
    PromptRead,
    PromptUpdate,
)
from app.services import eval_history
from app.services import settings as settings_service

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/settings", tags=["settings"])


def require_local_settings_write(request: Request) -> None:
    """Allow settings mutations only from direct loopback/test clients."""
    host = request.client.host if request.client else None
    if host in {"localhost", "testclient"}:
        return
    if host is not None:
        try:
            if ip_address(host).is_loopback:
                return
        except ValueError:
            pass

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="settings writes are only allowed from localhost",
    )


@router.get("/profiles", response_model=list[ProfileRead])
def get_profiles() -> list[ProfileRead]:
    """Effective (merged) model profiles, with overridden fields marked."""
    return settings_service.list_profiles()


@router.patch(
    "/profiles/{name}",
    response_model=ProfileRead,
    dependencies=[Depends(require_local_settings_write)],
)
def patch_profile(name: str, update: ProfileUpdate) -> ProfileRead:
    """Override editable fields of a profile; writes to profiles.local.yaml."""
    try:
        return settings_service.update_profile(name, update)
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown profile: {name!r}"
        ) from None
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


@router.get("/prompts", response_model=list[PromptRead])
def get_prompts() -> list[PromptRead]:
    """All editable prompt files in ai/prompts/."""
    return settings_service.list_prompts()


@router.get("/prompts/{name}", response_model=PromptRead)
def get_prompt(name: str) -> PromptRead:
    try:
        return settings_service.get_prompt(name)
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown prompt: {name!r}"
        ) from None


@router.put(
    "/prompts/{name}",
    response_model=PromptRead,
    dependencies=[Depends(require_local_settings_write)],
)
def put_prompt(name: str, update: PromptUpdate) -> PromptRead:
    """Overwrite a prompt file on disk. Takes effect on the next model call."""
    try:
        return settings_service.put_prompt(name, update.text)
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown prompt: {name!r}"
        ) from None


@router.post(
    "/evals/{suite}/run",
    response_model=EvalRunResult,
    dependencies=[Depends(require_local_settings_write)],
)
def run_eval(suite: str, db: Session = Depends(get_db)) -> EvalRunResult:
    """Run an eval suite synchronously and return per-case pass/fail + totals.

    The pass/fail totals are also persisted to the ``eval_runs`` history so a
    prompt/profile edit can be seen to help or regress over time.
    """
    try:
        result = settings_service.run_eval(suite)
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown eval suite: {suite!r}"
        ) from None
    eval_history.record_run(
        db, suite=result.suite, passed=result.passed, total=result.total
    )
    db.commit()
    return result


@router.get("/evals/runs", response_model=list[EvalRunRecord])
def list_eval_runs(
    suite: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> Sequence[EvalRun]:
    """Persisted eval-run history, newest-first (read-only; public like other reads)."""
    return eval_history.list_runs(db, suite=suite, limit=limit)
