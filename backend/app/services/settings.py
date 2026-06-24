"""Settings IO: model-profile overrides, prompt files, and eval runs.

One responsibility: read/write the AI subsystem's tunable surfaces. Profile edits
land in the gitignored ``profiles.local.yaml`` (the committed ``profiles.yaml`` is
never touched); prompt edits write straight to ``ai/prompts/*.md``; eval runs invoke
each suite's ``run()`` synchronously (single-user local app — no Celery).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from collections.abc import Callable
from typing import Any

import structlog
import yaml
from pydantic import ValidationError

from app.ai import gateway
from app.ai.evals import (
    run_breakdown_evals,
    run_evals,
    run_match_evals,
    run_summary_evals,
)
from app.schemas.settings import (
    EvalCaseResult,
    EvalRunResult,
    OllamaStatus,
    ProfileRead,
    ProfileUpdate,
    PromptRead,
)

logger = structlog.get_logger(__name__)

_EVAL_SUITES: dict[str, Callable[[], list[dict[str, Any]]]] = {
    "task_extraction": run_evals.run,
    "break_down_task": run_breakdown_evals.run,
    "project_matching": run_match_evals.run,
    "summary": run_summary_evals.run,
}


# --- Profiles ---------------------------------------------------------------


def _read_local() -> dict[str, dict[str, Any]]:
    if gateway.local_profiles_path().exists():
        return yaml.safe_load(gateway.local_profiles_path().read_text()) or {}
    return {}


def _write_local(local: dict[str, dict[str, Any]]) -> None:
    gateway.local_profiles_path().write_text(yaml.safe_dump(local, sort_keys=False))


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
    return [_profile_read(name, local) for name in gateway.load_raw_merged()]


def update_profile(name: str, update: ProfileUpdate) -> ProfileRead:
    """Write an override for ``name`` and return the new effective profile.

    Raises ``KeyError`` for an unknown profile (→ 404) and ``ValueError`` if the
    merged result wouldn't form a valid profile (→ 422).
    """
    merged = gateway.load_raw_merged()
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


def reset_profile_overrides(name: str, field: str | None = None) -> ProfileRead:
    """Drop a profile's override(s) from ``profiles.local.yaml`` and return the
    new effective profile.

    ``field`` clears a single override key; ``None`` clears every override for
    ``name``. No-op safe (returns the unchanged profile) when nothing is
    overridden. Raises ``KeyError`` for an unknown profile (→ 404).
    """
    merged = gateway.load_raw_merged()
    if name not in merged:
        raise KeyError(name)

    local = _read_local()
    overrides = local.get(name, {})
    if field is not None:
        removed = overrides.pop(field, None) is not None
    else:
        removed = bool(overrides)
        overrides.clear()
    if not overrides:
        local.pop(name, None)

    if removed:
        _write_local(local)
        gateway.reload_profiles()
        logger.info("profile_overrides_reset", profile=name, field=field)

    return _profile_read(name, _read_local())


# --- Prompts ----------------------------------------------------------------


def _prompt_path(name: str) -> Path:
    """Resolve ``name`` to an existing prompt file, rejecting traversal/unknowns.

    Raises ``KeyError`` if the name escapes ``prompts/`` or doesn't already exist.
    """
    if name != Path(name).name:  # no separators / traversal
        raise KeyError(name)
    path = gateway.prompts_dir() / name
    if not path.is_file():
        raise KeyError(name)
    return path


def list_prompts() -> list[PromptRead]:
    return [
        PromptRead(name=p.name, text=p.read_text())
        for p in sorted(gateway.prompts_dir().glob("*.md"))
    ]


def get_prompt(name: str) -> PromptRead:
    path = _prompt_path(name)
    return PromptRead(name=name, text=path.read_text())


def put_prompt(name: str, text: str) -> PromptRead:
    path = _prompt_path(name)  # also guards against traversal in ``name``
    _snapshot_prompt(name, path)
    path.write_text(text)
    logger.info("prompt_updated", prompt=name, chars=len(text))
    return PromptRead(name=name, text=text)


def _snapshot_prompt(name: str, path: Path) -> None:
    """Copy the current prompt content to ``.history/`` before it's overwritten.

    Lets a score drop after a prompt edit be diffed against the previous version
    and reverted manually. ``_prompt_path`` guarantees the file already exists, so
    there is always content to snapshot. The timestamp is filesystem-safe (no
    colons) so the snapshot reads back as a normal file on every platform; the
    ``.history`` subdirectory is not matched by ``list_prompts``' ``*.md`` glob.
    """
    history_dir = gateway.prompts_dir() / ".history"
    history_dir.mkdir(parents=True, exist_ok=True)
    # Microsecond precision so two saves in the same second don't collide.
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S_%f")
    snapshot = history_dir / f"{name}.{timestamp}.md"
    snapshot.write_text(path.read_text())
    logger.info("prompt_snapshot_saved", prompt=name, snapshot=str(snapshot))


# --- Provider introspection -------------------------------------------------


def ollama_status() -> OllamaStatus:
    reachable, host = gateway.ollama_status()
    return OllamaStatus(reachable=reachable, host=host)


def list_models() -> list[str]:
    return gateway.installed_models()


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
