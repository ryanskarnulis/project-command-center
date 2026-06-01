"""Settings IO: model-profile overrides, prompt files, and eval runs.

One responsibility: read/write the AI subsystem's tunable surfaces. Profile edits
land in the gitignored ``profiles.local.yaml`` (the committed ``profiles.yaml`` is
never touched); prompt edits write straight to ``ai/prompts/*.md``; eval runs invoke
each suite's ``run()`` synchronously (single-user local app — no Celery).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import structlog
import yaml
from pydantic import ValidationError

from app.ai import gateway
from app.ai.evals import run_evals, run_match_evals, run_summary_evals
from app.schemas.settings import (
    EvalCaseResult,
    EvalRunResult,
    ProfileRead,
    ProfileUpdate,
    PromptRead,
)

logger = structlog.get_logger(__name__)

_EVAL_SUITES: dict[str, Callable[[], list[dict[str, Any]]]] = {
    "task_extraction": run_evals.run,
    "project_matching": run_match_evals.run,
    "summary": run_summary_evals.run,
}


# --- Profiles ---------------------------------------------------------------


def _read_local() -> dict[str, dict[str, Any]]:
    if gateway._LOCAL_PROFILES_PATH.exists():
        return yaml.safe_load(gateway._LOCAL_PROFILES_PATH.read_text()) or {}
    return {}


def _write_local(local: dict[str, dict[str, Any]]) -> None:
    gateway._LOCAL_PROFILES_PATH.write_text(yaml.safe_dump(local, sort_keys=False))


def _profile_read(name: str, local: dict[str, dict[str, Any]]) -> ProfileRead:
    profile = gateway.get_profile(name)  # validated, effective (merged) values
    return ProfileRead(
        name=name,
        provider=profile.provider,
        model=profile.model,
        temperature=profile.temperature,
        max_tokens=profile.max_tokens,
        response_mode=profile.response_mode,
        system_prompt=profile.system_prompt,
        overridden_fields=sorted(local.get(name, {})),
    )


def list_profiles() -> list[ProfileRead]:
    local = _read_local()
    return [_profile_read(name, local) for name in gateway._load_raw_merged()]


def update_profile(name: str, update: ProfileUpdate) -> ProfileRead:
    """Write an override for ``name`` and return the new effective profile.

    Raises ``KeyError`` for an unknown profile (→ 404) and ``ValueError`` if the
    merged result wouldn't form a valid profile (→ 422).
    """
    merged = gateway._load_raw_merged()
    if name not in merged:
        raise KeyError(name)

    fields = update.model_dump(exclude_none=True)
    if fields:
        candidate = {**merged[name], **fields}
        try:
            gateway.Profile.model_validate(candidate)
        except ValidationError as exc:
            raise ValueError(str(exc)) from exc

        local = _read_local()
        local.setdefault(name, {}).update(fields)
        _write_local(local)
        gateway.reload_profiles()
        logger.info("profile_updated", profile=name, fields=sorted(fields))

    return _profile_read(name, _read_local())


# --- Prompts ----------------------------------------------------------------


def _prompt_path(name: str) -> Path:
    """Resolve ``name`` to an existing prompt file, rejecting traversal/unknowns.

    Raises ``KeyError`` if the name escapes ``prompts/`` or doesn't already exist.
    """
    if name != Path(name).name:  # no separators / traversal
        raise KeyError(name)
    path = gateway._PROMPTS_DIR / name
    if not path.is_file():
        raise KeyError(name)
    return path


def list_prompts() -> list[PromptRead]:
    return [
        PromptRead(name=p.name, text=p.read_text())
        for p in sorted(gateway._PROMPTS_DIR.glob("*.md"))
    ]


def get_prompt(name: str) -> PromptRead:
    path = _prompt_path(name)
    return PromptRead(name=name, text=path.read_text())


def put_prompt(name: str, text: str) -> PromptRead:
    path = _prompt_path(name)
    path.write_text(text)
    logger.info("prompt_updated", prompt=name, chars=len(text))
    return PromptRead(name=name, text=text)


# --- Evals ------------------------------------------------------------------


def run_eval(suite: str) -> EvalRunResult:
    """Run an eval suite synchronously. Raises ``KeyError`` for an unknown suite."""
    try:
        runner = _EVAL_SUITES[suite]
    except KeyError:
        raise KeyError(suite) from None

    log = logger.bind(suite=suite)
    log.info("eval_run_started")
    cases = [
        EvalCaseResult(name=r["name"], passed=r["passed"], reason=r["reason"])
        for r in runner()
    ]
    passed = sum(1 for c in cases if c.passed)
    log.info("eval_run_finished", passed=passed, total=len(cases))
    return EvalRunResult(suite=suite, passed=passed, total=len(cases), cases=cases)
