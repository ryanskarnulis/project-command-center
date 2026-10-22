#!/usr/bin/env python3
"""Probe the agent's first model call directly: one call per sample, arms interleaved (#410).

Phase 4 of the agent plan (#396) changes what the model sees one lever at a
time, and every lever needs a fair A/B. The eval gate answers "does the whole
run still work"; this answers the cheaper question first: "does the model open
differently?" It screens a prompt, tool-text, temperature or model change in
minutes, before the gate spends an hour on it.

What it sends is the shipped first call, not a copy of it. The messages, the
tool offer and the token cap come out of the real :class:`app.ai.loop.AgentLoop`
(a recording provider takes its first ``chat`` call and ends the run), and the
request goes out through the real :class:`LlamaCppProvider` at the model's
profile (#402) — so the system prompt, the tool schemas, the history window
and the request bytes cannot drift from production. An arm changes exactly the
knobs it names:

    prompt=FILE        replaces the system prompt with FILE's text (``{today}``
                       in the file becomes today's date; the state block in
                       the user turn is the loop's, unchanged)
    tools=FILE         JSON ``{"tool": "description", "tool.param": "text"}``
                       overriding a tool's, or one parameter's, description
    temperature=T      sampling temperature (the profile's otherwise)
    model=ID           another model alias, with that model's profile
    profile=NAME       another profile for the same alias

Each sample is classified from the wire result alone: ``tool:<name>`` per
call made (joined with `` + `` when a turn makes several), or ``no_tool``. No
language is parsed. A corpus item may say which classes are the pass
(``expect``, fnmatch patterns): the table then carries a pass count and, per
arm after the first, the exact McNemar p against the first on the pairs the
change flipped.

**Why arms interleave.** On one llama-server, consecutive samples of one
prompt are correlated (chess measured one unchanged prompt at 5/20, 19/40 and
31/40 in three separate batches). So for every (item, sample) all arms run
back to back — rotated so no arm is always first — with the same sampling seed
(``--seed``, paired), and a batch of one arm alone is not something this
script can produce. See ``docs/agent-campaigns.md``.

Run from ``backend/`` with llama-swap up (``LLAMACPP_BASE_URL`` /
``LLAMACPP_MODEL`` as for the app):

    .venv/bin/python scripts/probe_first_call.py --arm control \\
        --arm cold:temperature=0.2 --n 20
    .venv/bin/python scripts/probe_first_call.py --arm control \\
        --arm new:prompt=/tmp/prompt.txt,tools=/tmp/tools.json \\
        --items corpus.json --n 40 --out /tmp/probe.jsonl
    .venv/bin/python scripts/probe_first_call.py --arm control --dry-run   # the exact payload

An items file is a JSON list of ``{"id", "ask", "history"?, "expect"?}``.
"""

from __future__ import annotations

import argparse
import copy
import fnmatch
import json
import logging
import statistics
import sys
import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import structlog

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.ai import loop as agent_loop  # noqa: E402
from app.ai import trace as run_trace  # noqa: E402
from app.ai.model_profile import ModelProfile, load_profile  # noqa: E402
from app.ai.providers.llamacpp import (  # noqa: E402
    ChatResult,
    LlamaCppProvider,
    ProviderError,
    ToolCallArgumentsError,
    ToolSpec,
)
from app.config import get_settings  # noqa: E402
from tests.evalstats import format_paired, paired_counts  # noqa: E402

NO_TOOL = "no_tool"
DEFAULT_SEED = 410_000
ARM_KEYS = ("prompt", "tools", "temperature", "model", "profile")


# --- corpus -------------------------------------------------------------------


@dataclass(frozen=True)
class Item:
    """One user ask, with the classes that count as a pass (none: unscored)."""

    id: str
    ask: str
    history: tuple[dict[str, Any], ...] = ()
    expect: tuple[str, ...] = ()


# A starter corpus: enough to smoke the tool. Real screens bring their own
# (`--items`), built from the scenario or the deployed misfire under study.
DEFAULT_ITEMS: tuple[Item, ...] = (
    Item("greeting", "hi there, thanks for the help!", expect=(NO_TOOL,)),
    Item("plate", "what's on my plate today?", expect=("tool:*",)),
    Item("add_task", "add a task to buy milk", expect=("tool:*",)),
)


def load_items(path: Path) -> list[Item]:
    """Items from a JSON file; strict, because a typo here is a quietly wrong screen."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not raw:
        raise SystemExit(f"{path}: expected a non-empty JSON list of items")
    items: list[Item] = []
    for entry in raw:
        unknown = set(entry) - {"id", "ask", "history", "expect"}
        if unknown or "id" not in entry or "ask" not in entry:
            raise SystemExit(
                f"{path}: item {entry!r} needs id and ask, and only id/ask/history/expect"
            )
        items.append(
            Item(
                id=str(entry["id"]),
                ask=str(entry["ask"]),
                history=tuple(entry.get("history", ())),
                expect=tuple(entry.get("expect", ())),
            )
        )
    if len({item.id for item in items}) != len(items):
        raise SystemExit(f"{path}: item ids must be unique")
    return items


# --- arms ---------------------------------------------------------------------


@dataclass(frozen=True)
class Arm:
    """A named variant of the shipped first call. All-``None`` is the control."""

    name: str
    prompt: Path | None = None
    tools: Path | None = None
    temperature: float | None = None
    model: str | None = None
    profile: str | None = None


def parse_arm(spec: str) -> Arm:
    """``name`` or ``name:key=value,key=value`` (keys: :data:`ARM_KEYS`)."""
    name, _, rest = spec.partition(":")
    if not name:
        raise ValueError(f"arm {spec!r} has no name")
    fields: dict[str, str] = {}
    for part in filter(None, rest.split(",")):
        key, eq, value = part.partition("=")
        if not eq or key not in ARM_KEYS or not value:
            raise ValueError(f"arm {name!r}: bad setting {part!r} (keys: {', '.join(ARM_KEYS)})")
        if key in fields:
            raise ValueError(f"arm {name!r}: {key} given twice")
        fields[key] = value
    return Arm(
        name=name,
        prompt=Path(fields["prompt"]) if "prompt" in fields else None,
        tools=Path(fields["tools"]) if "tools" in fields else None,
        temperature=float(fields["temperature"]) if "temperature" in fields else None,
        model=fields.get("model"),
        profile=fields.get("profile"),
    )


def apply_tool_overrides(specs: Sequence[ToolSpec], overrides: dict[str, str]) -> list[ToolSpec]:
    """Copies of ``specs`` with ``overrides`` applied: ``"tool"`` replaces a tool's
    description, ``"tool.param"`` one parameter's. An override that names
    nothing is an error — it would be a control mislabelled as a change."""
    out = [spec.model_copy(update={"parameters": copy.deepcopy(spec.parameters)}) for spec in specs]
    by_name = {spec.name: spec for spec in out}
    for key, text in overrides.items():
        tool, _, param = key.partition(".")
        spec = by_name.get(tool)
        if spec is None:
            raise ValueError(f"tool override {key!r}: no tool named {tool!r}")
        if not param:
            spec.description = text
            continue
        properties = spec.parameters.get("properties", {})
        if param not in properties:
            raise ValueError(f"tool override {key!r}: {tool} has no parameter {param!r}")
        properties[param]["description"] = text
    return out


def override_system_prompt(
    messages: Sequence[dict[str, Any]], text: str, today: date
) -> list[dict[str, Any]]:
    """``messages`` with the system message's content replaced by ``text``.

    The system prompt carries no date since #412 (the state block does), so
    none is appended; ``{today}`` in ``text`` is still filled in."""
    content = text.strip().replace("{today}", today.isoformat())
    out = [dict(message) for message in messages]
    if not out or out[0].get("role") != "system":
        raise ValueError("the loop's first message is no longer the system prompt")
    out[0]["content"] = content
    return out


def arm_profile(arm: Arm, default_model: str) -> ModelProfile:
    """The profile an arm runs at: its ``profile=``/``model=``, else the app's,
    with ``temperature=`` laid over the sampling."""
    profile = load_profile(arm.profile or arm.model or default_model)
    if arm.temperature is not None:
        sampling = profile.sampling.model_copy(update={"temperature": arm.temperature})
        profile = profile.model_copy(update={"sampling": sampling})
    return profile


# --- the shipped first call ---------------------------------------------------


@dataclass(frozen=True)
class FirstCall:
    """What the loop would send first: messages, tool offer, per-call cap."""

    messages: tuple[dict[str, Any], ...]
    tools: tuple[ToolSpec, ...]
    max_tokens: int


class _Recorder:
    """A provider that records the loop's first ``chat`` call and ends the run
    with a plain reply — the loop itself builds everything we capture."""

    def __init__(self, profile: ModelProfile, model: str) -> None:
        self.profile = profile
        self.model = model
        self.first: FirstCall | None = None

    def chat(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        tools: Sequence[ToolSpec] | None = None,
        enable_thinking: bool = False,
        max_tokens: int | None = None,
        timeout: float | None = None,
    ) -> ChatResult:
        if self.first is None:
            assert max_tokens is not None
            self.first = FirstCall(
                tuple(copy.deepcopy(list(messages))), tuple(tools or ()), max_tokens
            )
        return ChatResult(content="ok", tool_calls=[], finish_reason="stop", usage=None)


class _NoTrace:
    def write(self, record: dict[str, Any]) -> None:
        return None


def capture_first_call(item: Item, profile: ModelProfile, model: str) -> FirstCall:
    """Run the real loop against a recorder and keep its first model call."""
    recorder = _Recorder(profile, model)
    tracer: run_trace.Tracer = _NoTrace()
    loop = agent_loop.AgentLoop(recorder, tracer=tracer)
    loop.run(item.ask, history=list(item.history) or None)
    if recorder.first is None:
        raise RuntimeError("the loop made no model call")
    return recorder.first


def build_arm_call(
    arm: Arm, item: Item, default_model: str, today: date
) -> tuple[FirstCall, ModelProfile, str]:
    """The first call as ``arm`` sends it: (call, profile, model alias)."""
    model = arm.model or default_model
    profile = arm_profile(arm, default_model)
    call = capture_first_call(item, profile, model)
    messages: Sequence[dict[str, Any]] = call.messages
    tools: Sequence[ToolSpec] = call.tools
    if arm.prompt is not None:
        messages = override_system_prompt(messages, arm.prompt.read_text(encoding="utf-8"), today)
    if arm.tools is not None:
        overrides = json.loads(arm.tools.read_text(encoding="utf-8"))
        if not isinstance(overrides, dict) or not all(
            isinstance(v, str) for v in overrides.values()
        ):
            raise ValueError(f"{arm.tools}: expected a JSON object of text overrides")
        tools = apply_tool_overrides(tools, overrides)
    return FirstCall(tuple(messages), tuple(tools), call.max_tokens), profile, model


# --- classification -----------------------------------------------------------


def classify(result: ChatResult) -> tuple[str, ...]:
    """The wire result's class: ``tool:<name>`` per call, or ``no_tool``."""
    if not result.tool_calls:
        return (NO_TOOL,)
    return tuple(f"tool:{call.name}" for call in result.tool_calls)


def label(classes: Sequence[str]) -> str:
    """A class tuple as one table key."""
    return " + ".join(classes)


def classify_error(exc: Exception) -> str:
    """A call that did not produce a result. A tool call whose arguments were
    not JSON is still the model choosing a tool (``bad_args:<name>``); anything
    else is the server, not the model (``error:<type>``), and is not scored."""
    if isinstance(exc, ToolCallArgumentsError):
        return f"bad_args:{exc.tool_name}"
    return f"error:{type(exc).__name__}"


def is_infra(klass: str) -> bool:
    return klass.startswith("error:")


def passes(expect: Sequence[str], klass: str) -> bool:
    return any(fnmatch.fnmatchcase(klass, pattern) for pattern in expect)


# --- sampling -----------------------------------------------------------------


@dataclass(frozen=True)
class Sample:
    arm: str
    item: str
    index: int
    seed: int | None
    klass: str
    finish_reason: str | None
    ms: int
    completion_tokens: int | None


def arm_order(arms: Sequence[Arm], index: int) -> list[Arm]:
    """The arms in the order sample ``index`` runs them: rotated by one each
    sample, so no arm is always first and server drift lands on all alike."""
    shift = index % len(arms)
    return [*arms[shift:], *arms[:shift]]


def seed_for(base: int | None, index: int) -> int | None:
    """Sample ``index``'s seed, the same for every arm (the pairing)."""
    return None if base is None else base + index


def take_sample(
    arm: Arm,
    item: Item,
    index: int,
    seed: int | None,
    *,
    base_url: str,
    default_model: str,
    client: httpx.Client,
    built: dict[tuple[str, str], tuple[FirstCall, ModelProfile, str]] | None = None,
) -> Sample:
    """One timed call. ``built`` memoizes each (arm, item)'s request: the loop
    is run to build it once, not once per sample."""
    cache = built if built is not None else {}
    key = (arm.name, item.id)
    if key not in cache:
        cache[key] = build_arm_call(arm, item, default_model, date.today())
    call, profile, model = cache[key]
    started = time.perf_counter()
    finish: str | None = None
    tokens: int | None = None
    provider = LlamaCppProvider(base_url, model, client=client, profile=profile, seed=seed)
    try:
        result = provider.chat(call.messages, tools=call.tools, max_tokens=call.max_tokens)
        klass = label(classify(result))
        finish = result.finish_reason
        tokens = result.usage.completion_tokens if result.usage else None
    except ProviderError as exc:
        klass = classify_error(exc)
    return Sample(
        arm.name, item.id, index, seed, klass, finish,
        round((time.perf_counter() - started) * 1000), tokens,
    )  # fmt: skip


# --- the table ----------------------------------------------------------------


@dataclass
class ArmTally:
    classes: Counter[str] = field(default_factory=Counter)
    errors: int = 0
    ms: list[int] = field(default_factory=list)

    @property
    def n(self) -> int:
        return sum(self.classes.values())


def tally(samples: Sequence[Sample], arm: str) -> ArmTally:
    out = ArmTally()
    for sample in samples:
        if sample.arm != arm:
            continue
        if is_infra(sample.klass):
            out.errors += 1
        else:
            out.classes[sample.klass] += 1
            out.ms.append(sample.ms)
    return out


def paired_verdict(samples: Sequence[Sample], item: Item, control: str, arm: str) -> str:
    """The McNemar line for ``arm`` against ``control`` on the samples both
    scored at the same index (needs ``expect``)."""

    def outcomes(name: str) -> dict[int, bool]:
        return {
            s.index: passes(item.expect, s.klass)
            for s in samples
            if s.arm == name and s.item == item.id and not is_infra(s.klass)
        }

    a, b = outcomes(control), outcomes(arm)
    shared = sorted(a.keys() & b.keys())
    if not shared:
        return "no shared samples"
    _, a_only, b_only, _ = paired_counts([a[i] for i in shared], [b[i] for i in shared])
    return format_paired(a_only, b_only, len(shared))


def format_tables(samples: Sequence[Sample], arms: Sequence[Arm], items: Sequence[Item]) -> str:
    """One table per item: each arm's n, passes (if scored), median latency,
    its class counts, and the paired verdict against the first arm."""
    lines: list[str] = []
    for item in items:
        mine = [s for s in samples if s.item == item.id]
        lines.append(
            f"\n== {item.id}: {item.ask!r}"
            + (f"  (pass: {', '.join(item.expect)})" if item.expect else "")
        )
        width = max(len(arm.name) for arm in arms)
        for arm in arms:
            t = tally(mine, arm.name)
            scored = (
                f" pass {sum(c for k, c in t.classes.items() if passes(item.expect, k))}/{t.n}"
                if item.expect
                else ""
            )
            err = f" errors {t.errors}" if t.errors else ""
            median = f" {statistics.median(t.ms):.0f}ms" if t.ms else ""
            dist = " · ".join(f"{k} {c}" for k, c in t.classes.most_common())
            line = f"{arm.name.ljust(width)}  n={t.n}{scored}{err}{median}  {dist}"
            if item.expect and arm is not arms[0]:
                line += (
                    f"   vs {arms[0].name}: {paired_verdict(mine, item, arms[0].name, arm.name)}"
                )
            lines.append(line)
    return "\n".join(lines)


# --- dry run ------------------------------------------------------------------


def dry_run_payload(arm: Arm, item: Item, default_model: str, seed: int | None) -> dict[str, Any]:
    """The exact request body ``arm`` would send for ``item``, built by the real
    provider against a transport that answers instead of a server."""
    captured: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request.content)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]},
        )

    call, profile, model = build_arm_call(arm, item, default_model, date.today())
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        LlamaCppProvider(
            "http://dry.run/v1", model, client=client, profile=profile, seed=seed
        ).chat(call.messages, tools=call.tools, max_tokens=call.max_tokens)
    payload: dict[str, Any] = json.loads(captured[0])
    return payload


def describe_arm(arm: Arm, default_model: str, item: Item) -> str:
    call, profile, model = build_arm_call(arm, item, default_model, date.today())
    system = str(call.messages[0]["content"])
    return (
        f"arm {arm.name}: model={model} profile={profile.name} "
        f"sampling={profile.sampling.wire()} max_tokens={call.max_tokens} "
        f"system_sha={run_trace.sha256(system)[:12]} "
        f"tools_sha={run_trace.tool_schemas_sha256(list(call.tools))[:12]} "
        f"tools={len(call.tools)}"
    )


# --- main ---------------------------------------------------------------------


def _parse_seed(raw: str) -> int | None:
    return None if raw.lower() == "none" else int(raw)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Probe the shipped first model call, arms interleaved per sample.",
        epilog=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--arm",
        action="append",
        default=[],
        metavar="NAME[:k=v,...]",
        help="an arm; the first is the control the others are paired against",
    )
    parser.add_argument("--n", type=int, default=10, help="samples per arm per item (default 10)")
    parser.add_argument("--items", type=Path, help="JSON corpus (default: a three-item smoke set)")
    parser.add_argument("--ask", action="append", default=[], help="an inline ask, unscored")
    parser.add_argument(
        "--seed",
        type=_parse_seed,
        default=DEFAULT_SEED,
        help=f"base sampling seed, paired across arms; 'none' to omit (default {DEFAULT_SEED})",
    )
    parser.add_argument("--out", type=Path, help="append one JSON line per sample here")
    parser.add_argument(
        "--dry-run", action="store_true", help="print each arm's exact request, send nothing"
    )
    args = parser.parse_args(argv)
    # The app's per-call info logs (and one per loop run) would bury the table.
    structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING))

    if not args.arm:
        parser.error("need at least one --arm (try --arm control)")
    arms = [parse_arm(spec) for spec in args.arm]
    if len({arm.name for arm in arms}) != len(arms):
        parser.error("arm names must be unique")
    if args.items:
        items = load_items(args.items)
    elif args.ask:
        items = []
    else:
        items = list(DEFAULT_ITEMS)
    items += [Item(f"ask{i + 1}", ask) for i, ask in enumerate(args.ask)]

    settings = get_settings()
    base_url, default_model = settings.llamacpp_base_url, settings.llamacpp_model

    if args.dry_run:
        for arm in arms:
            print(describe_arm(arm, default_model, items[0]))
            print(json.dumps(dry_run_payload(arm, items[0], default_model, args.seed), indent=2))
        return 0

    running = _running(base_url)
    print(f"[probe] {base_url} model={default_model} running={running}")
    for arm in arms:
        print(f"[probe] {describe_arm(arm, default_model, items[0])}")
    print(f"[probe] {len(items)} item(s) x {len(arms)} arm(s) x n={args.n}, seed={args.seed}")

    samples: list[Sample] = []
    built: dict[tuple[str, str], tuple[FirstCall, ModelProfile, str]] = {}
    with httpx.Client(
        timeout=httpx.Timeout(settings.llamacpp_timeout_seconds, connect=10.0)
    ) as client:
        for item in items:
            for index in range(args.n):
                seed = seed_for(args.seed, index)
                for arm in arm_order(arms, index):
                    sample = take_sample(
                        arm, item, index, seed,
                        base_url=base_url, default_model=default_model, client=client, built=built,
                    )  # fmt: skip
                    samples.append(sample)
                    if args.out:
                        with args.out.open("a", encoding="utf-8") as handle:
                            handle.write(json.dumps(sample.__dict__) + "\n")
    print(format_tables(samples, arms, items))
    return 0


def _running(base_url: str) -> str:
    try:
        return httpx.get(f"{base_url.removesuffix('/v1')}/running", timeout=5).text.strip()
    except httpx.HTTPError as exc:
        raise SystemExit(f"[probe] llama-swap unreachable at {base_url}: {exc}") from exc


if __name__ == "__main__":
    raise SystemExit(main())
