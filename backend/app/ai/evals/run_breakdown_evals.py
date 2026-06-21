from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from app.ai import gateway
from app.ai.schemas import BreakdownInput, BreakdownOutput

_CASES_PATH = Path(__file__).parent / "breakdown_cases.yaml"
_DEFAULT_PROFILE = "break_down_task"


def _load_cases() -> list[dict[str, Any]]:
    return list(yaml.safe_load(_CASES_PATH.read_text()))


def _check_expect(output: BreakdownOutput, expect: dict[str, Any]) -> tuple[bool, str]:
    """Return (passed, reason). ``reason`` is empty when passed."""
    titles = [sub.title.lower() for sub in output.subtasks]
    confidences = [sub.confidence for sub in output.subtasks]

    min_subtasks = expect.get("min_subtasks")
    if min_subtasks is not None and len(output.subtasks) < min_subtasks:
        return False, f"expected >= {min_subtasks} subtasks, got {len(output.subtasks)}"

    max_subtasks = expect.get("max_subtasks")
    if max_subtasks is not None and len(output.subtasks) > max_subtasks:
        return False, f"expected <= {max_subtasks} subtasks, got {len(output.subtasks)}"

    for substr in expect.get("title_contains", []):
        if not any(substr.lower() in title for title in titles):
            return False, f"no subtask title contains {substr!r}"

    if "needs_review" in expect and output.needs_review != expect["needs_review"]:
        return False, f"needs_review expected {expect['needs_review']}, got {output.needs_review}"

    if expect.get("confidence_varies") and len(confidences) >= 2:
        if len(set(confidences)) == 1:
            return False, f"all subtask confidences equal ({confidences[0]})"

    if expect.get("low_confidence_present"):
        if not any(c < 0.8 for c in confidences):
            return False, "no subtask has confidence < 0.8"

    return True, ""


def _run_case(case: dict[str, Any], profile: str, model: str | None) -> tuple[bool, str]:
    """Run a single case once. Returns (passed, reason)."""
    user_content = BreakdownInput(
        title=case["title"],
        description=case.get("description"),
    ).to_user_content()
    raw = gateway.complete(
        profile_name=profile,
        user_content=user_content,
        json_schema=BreakdownOutput.model_json_schema(),
        model_override=model,
    )
    try:
        output = BreakdownOutput.model_validate_json(raw)
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
    parser = argparse.ArgumentParser(description="Run break_down_task eval cases.")
    parser.add_argument(
        "--profile", default=_DEFAULT_PROFILE, help="profile name (default: break_down_task)"
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
