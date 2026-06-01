from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from app.ai import gateway
from app.ai.schemas import ExtractionInput, ExtractionOutput

_CASES_PATH = Path(__file__).parent / "extraction_cases.yaml"
_DEFAULT_PROFILE = "task_extraction"


def _load_cases() -> list[dict[str, Any]]:
    return list(yaml.safe_load(_CASES_PATH.read_text()))


def _check_expect(output: ExtractionOutput, expect: dict[str, Any]) -> tuple[bool, str]:
    """Return (passed, reason). ``reason`` is empty when passed."""
    titles = [task.title.lower() for task in output.tasks]
    confidences = [task.confidence for task in output.tasks]

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

    # Trivially satisfied with <2 tasks: there's nothing to vary.
    if expect.get("confidence_varies") and len(confidences) >= 2:
        if len(set(confidences)) == 1:
            return False, f"all task confidences equal ({confidences[0]})"

    if expect.get("low_confidence_present"):
        if not any(c < 0.8 for c in confidences):
            return False, "no task has confidence < 0.8"

    return True, ""


def _run_case(case: dict[str, Any], profile: str, model: str | None) -> tuple[bool, str]:
    """Run a single case once. Returns (passed, reason)."""
    user_content = ExtractionInput(
        raw_text=case["raw_text"],
        today=date.fromisoformat(case["today"]),
    ).to_user_content()
    raw = gateway.complete(
        profile_name=profile,
        user_content=user_content,
        json_schema=ExtractionOutput.model_json_schema(),
        model_override=model,
    )
    try:
        output = ExtractionOutput.model_validate_json(raw)
    except ValidationError as exc:
        return False, f"invalid output: {exc}"
    return _check_expect(output, case.get("expect", {}))


def run() -> list[dict[str, Any]]:
    """Programmatic entry point used by the settings eval runner.

    Runs each case once on the default profile. Returns one row per case:
    ``{"name": str, "passed": bool, "reason": str}``.
    """
    results: list[dict[str, Any]] = []
    for case in _load_cases():
        ok, reason = _run_case(case, _DEFAULT_PROFILE, None)
        results.append({"name": case["name"], "passed": ok, "reason": reason})
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Run task_extraction eval cases.")
    parser.add_argument(
        "--profile", default=_DEFAULT_PROFILE, help="profile name (default: task_extraction)"
    )
    parser.add_argument(
        "--model", default=None, help="override the profile's model (benchmarking)"
    )
    parser.add_argument(
        "--repeat", type=int, default=1, help="runs per case; a case passes only if all pass"
    )
    args = parser.parse_args()

    cases = _load_cases()
    failures = 0
    model_label = args.model or f"profile:{args.profile}"
    print(f"Running {len(cases)} cases ({model_label}, repeat={args.repeat})\n")

    for case in cases:
        name = case["name"]
        passes = 0
        first_reason = ""
        for _ in range(args.repeat):
            ok, reason = _run_case(case, args.profile, args.model)
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
    print(f"\n{model_label}: {total - failures}/{total} cases passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
