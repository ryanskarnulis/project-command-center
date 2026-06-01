from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml

from app.ai.schemas import ProjectChoice
from app.ai.workflows.match_project import match_project_ai

_CASES_PATH = Path(__file__).parent / "match_cases.yaml"


def _load_cases() -> list[dict[str, Any]]:
    return list(yaml.safe_load(_CASES_PATH.read_text()))


def _run_case(case: dict[str, Any]) -> tuple[bool, str]:
    """Run a single case once. Returns (passed, reason)."""
    choices = [ProjectChoice(**project) for project in case["projects"]]
    result, _, raw = match_project_ai(
        project_hint=case["project_hint"],
        summary=case.get("summary"),
        task_titles=case.get("task_titles", []),
        choices=choices,
    )
    expected = case.get("expect", {}).get("project_id")
    if result is None:
        # Invalid JSON or a non-offered id — counts as "no match".
        if expected is None:
            return True, ""
        return False, f"no valid match (expected {expected}); raw={raw!r}"
    if result.project_id != expected:
        return False, f"expected project_id {expected}, got {result.project_id}"
    return True, ""


def run() -> list[dict[str, Any]]:
    """Programmatic entry point used by the settings eval runner.

    Runs each case once. Returns one row per case:
    ``{"name": str, "passed": bool, "reason": str}``.
    """
    results: list[dict[str, Any]] = []
    for case in _load_cases():
        ok, reason = _run_case(case)
        results.append({"name": case["name"], "passed": ok, "reason": reason})
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Run project_matching eval cases.")
    parser.add_argument(
        "--repeat", type=int, default=1, help="runs per case; a case passes only if all pass"
    )
    args = parser.parse_args()

    cases = _load_cases()
    failures = 0
    print(f"Running {len(cases)} match cases (profile:project_matching, repeat={args.repeat})\n")

    for case in cases:
        name = case["name"]
        passes = 0
        first_reason = ""
        for _ in range(args.repeat):
            ok, reason = _run_case(case)
            if ok:
                passes += 1
            elif not first_reason:
                first_reason = reason
        if passes == args.repeat:
            print(f"PASS {name} ({passes}/{args.repeat})")
        else:
            print(f"FAIL {name} ({passes}/{args.repeat}): {first_reason}")
            failures += 1

    total = len(cases)
    print(f"\nproject_matching: {total - failures}/{total} cases passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
