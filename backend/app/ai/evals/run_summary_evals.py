from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from app.ai import gateway
from app.ai.schemas import SummaryInput, SummaryTaskRow
from app.db.models import TaskPriority, TaskWorkflowStatus

_CASES_PATH = Path(__file__).parent / "summary_cases.yaml"
_DEFAULT_PROFILE = "summary"


def _load_cases() -> list[dict[str, Any]]:
    return list(yaml.safe_load(_CASES_PATH.read_text()))


def _check_expect(text: str, expect: dict[str, Any]) -> tuple[bool, str]:
    if expect.get("not_empty") and not text.strip():
        return False, "summary is empty"

    min_chars = expect.get("min_chars")
    if min_chars is not None and len(text) < min_chars:
        return False, f"expected >= {min_chars} chars, got {len(text)}"

    max_chars = expect.get("max_chars")
    if max_chars is not None and len(text) > max_chars:
        return False, f"expected <= {max_chars} chars, got {len(text)}"

    for substr in expect.get("contains", []):
        if substr.lower() not in text.lower():
            return False, f"summary does not contain {substr!r}"

    return True, ""


def _run_case(case: dict[str, Any], profile: str) -> tuple[bool, str]:
    task_rows = [
        SummaryTaskRow(
            title=t["title"],
            workflow_status=TaskWorkflowStatus(t["workflow_status"]),
            priority=TaskPriority(t["priority"]),
            due_date=date.fromisoformat(t["due_date"]) if t.get("due_date") else None,
        )
        for t in case.get("tasks", [])
    ]
    user_content = SummaryInput(
        project_name=case["project_name"],
        tasks=task_rows,
        today=date.fromisoformat(case["today"]),
    ).to_user_content()

    text = gateway.complete(profile_name=profile, user_content=user_content)
    return _check_expect(text, case.get("expect", {}))


def run() -> list[dict[str, Any]]:
    """Programmatic entry point used by the settings eval runner."""
    cases = _load_cases()
    results: list[dict[str, Any]] = []
    for case in cases:
        ok, reason = _run_case(case, _DEFAULT_PROFILE)
        results.append({"name": case["name"], "passed": ok, "reason": reason})
    return results


def main() -> int:
    cases = _load_cases()
    failures = 0
    print(f"Running {len(cases)} summary cases (profile: {_DEFAULT_PROFILE})\n")
    for case in cases:
        ok, reason = _run_case(case, _DEFAULT_PROFILE)
        if ok:
            print(f"PASS {case['name']}")
        else:
            print(f"FAIL {case['name']}: {reason}")
            failures += 1
    total = len(cases)
    print(f"\n{total - failures}/{total} cases passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
