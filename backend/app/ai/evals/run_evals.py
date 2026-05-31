from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from app.ai import gateway
from app.ai.schemas import ExtractionInput, ExtractionOutput

_CASES_PATH = Path(__file__).parent / "extraction_cases.yaml"
_PROFILE = "task_extraction"


def _load_cases() -> list[dict[str, Any]]:
    return list(yaml.safe_load(_CASES_PATH.read_text()))


def _check_expect(output: ExtractionOutput, expect: dict[str, Any]) -> tuple[bool, str]:
    """Return (passed, reason). ``reason`` is empty when passed."""
    titles = [task.title.lower() for task in output.tasks]

    min_tasks = expect.get("min_tasks")
    if min_tasks is not None and len(output.tasks) < min_tasks:
        return False, f"expected >= {min_tasks} tasks, got {len(output.tasks)}"

    max_tasks = expect.get("max_tasks")
    if max_tasks is not None and len(output.tasks) > max_tasks:
        return False, f"expected <= {max_tasks} tasks, got {len(output.tasks)}"

    for substr in expect.get("title_contains", []):
        if not any(substr.lower() in title for title in titles):
            return False, f"no task title contains {substr!r}"

    if "project_hint_present" in expect:
        present = output.project_hint is not None
        if present != expect["project_hint_present"]:
            return False, f"project_hint_present expected {expect['project_hint_present']}"

    if "assignee_hint_present" in expect:
        present = any(task.assignee_hint is not None for task in output.tasks)
        if present != expect["assignee_hint_present"]:
            return False, f"assignee_hint_present expected {expect['assignee_hint_present']}"

    if "needs_review" in expect and output.needs_review != expect["needs_review"]:
        return False, f"needs_review expected {expect['needs_review']}, got {output.needs_review}"

    priority_in = expect.get("priority_in")
    if priority_in is not None:
        allowed = set(priority_in)
        if not any(task.priority in allowed for task in output.tasks):
            return False, f"no task has a priority in {priority_in}"

    return True, ""


def main() -> int:
    cases = _load_cases()
    failures = 0

    for case in cases:
        name = case["name"]
        user_content = ExtractionInput(
            raw_text=case["raw_text"],
            today=date.fromisoformat(case["today"]),
        ).to_user_content()
        raw = gateway.complete(
            profile_name=_PROFILE,
            user_content=user_content,
            json_schema=ExtractionOutput.model_json_schema(),
        )
        try:
            output = ExtractionOutput.model_validate_json(raw)
        except ValidationError as exc:
            print(f"FAIL {name}: invalid output: {exc}")
            failures += 1
            continue

        ok, reason = _check_expect(output, case.get("expect", {}))
        if ok:
            print(f"PASS {name}")
        else:
            print(f"FAIL {name}: {reason}")
            failures += 1

    total = len(cases)
    print(f"\n{total - failures}/{total} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
