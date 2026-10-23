#!/usr/bin/env python3
"""Summarise or pretty-print the agent's run trace (#400).

The trace is the JSONL file ``PCC_TRACE_PATH`` names: one record per agent run
(``app/ai/trace.py``; layout in ``docs/agent-tracing.md``). Standard library
only, so it runs anywhere the file is — no venv, no app import.

    python backend/scripts/read_trace.py data/traces/agent.jsonl
    python backend/scripts/read_trace.py data/traces/agent.jsonl --last 5
    python backend/scripts/read_trace.py data/traces/agent.jsonl --stop no_progress
    python backend/scripts/read_trace.py data/traces/agent.jsonl --run 69deb231
    python backend/scripts/read_trace.py data/traces/agent.jsonl --run 69deb231 --json

With no ``--run`` it prints one line per run, oldest first, then totals.
``--run`` (a run id or a prefix of one) prints that run in full: the ask, the
history, every model call and every tool call. ``--json`` prints the selected
records as indented JSON instead.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

# The record layouts this reader understands (``TRACE_SCHEMA`` in trace.py).
KNOWN_SCHEMAS = {1, 2, 3}
# The registry's read-only tools (``registry.read_only_tool_names()``), by the
# naming they all follow — this reader imports nothing from the app.
_READ_PREFIXES = ("list_", "get_")
_READ_NAMES = {"search"}
KIND_RUN = "run"
_CLIP = 100


def read_records(path: Path) -> Iterator[dict[str, Any]]:
    """Every run record in the file, skipping (and reporting) a bad line.

    A crash mid-write can leave a torn last line; that costs one record, never
    the read.
    """
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                print(f"line {number}: not JSON ({exc.msg}), skipped", file=sys.stderr)
                continue
            if not isinstance(record, dict) or record.get("kind") != KIND_RUN:
                continue
            if record.get("schema") not in KNOWN_SCHEMAS:
                print(
                    f"line {number}: schema {record.get('schema')!r} is newer than this "
                    "reader; reading what it can",
                    file=sys.stderr,
                )
            yield record


def clip(text: object, limit: int = _CLIP) -> str:
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def is_read(tool: str) -> bool:
    return tool.startswith(_READ_PREFIXES) or tool in _READ_NAMES


def lookup_turns(record: dict[str, Any]) -> int:
    """Model turns of only reads before the first turn that asked for anything
    else (#412). Rebuilt from ``calls`` and ``tools`` for every schema — each
    turn's ``tool_calls`` count takes that many records off the flat tool list
    — so a schema-1 baseline and a schema-2 run are counted the same way."""
    tools = [str(tool.get("name")) for tool in record.get("tools") or []]
    turns = 0
    at = 0
    for call in record.get("calls") or []:
        count = int(call.get("tool_calls") or 0)
        if call.get("phase") not in ("turn", "planner") or count == 0:
            continue
        names = tools[at : at + count]
        at += count
        if not names or not all(is_read(name) for name in names):
            break
        turns += 1
    return turns


def summary_line(record: dict[str, Any]) -> str:
    stop = str(record.get("stop_reason"))
    if record.get("summarised"):  # schema <= 3
        stop += "+summary"
    elif "narrated" in record and not record["narrated"] and record.get("reply") is not None:
        stop += "+note"
    conversation = record.get("conversation_id")
    return (
        f"{str(record.get('ts', ''))[:19]}  {str(record.get('run_id', ''))[:8]}  "
        f"conv={conversation if conversation is not None else '-':<4} "
        f"{stop:<24} calls={record.get('model_calls', 0):<2} "
        f"tools={len(record.get('tools') or []):<2} "
        f"lookups={lookup_turns(record)} "
        f"tok={record.get('prompt_tokens', 0)}/{record.get('completion_tokens', 0)} "
        f"{record.get('total_ms', 0)}ms  {clip(record.get('utterance', ''), 60)!r}"
    )


def totals(records: Sequence[dict[str, Any]]) -> str:
    stops: dict[str, int] = {}
    for record in records:
        key = str(record.get("stop_reason"))
        stops[key] = stops.get(key, 0) + 1
    by_stop = ", ".join(f"{key} {count}" for key, count in sorted(stops.items()))
    calls = sum(int(record.get("model_calls", 0)) for record in records)
    prompt = sum(int(record.get("prompt_tokens", 0)) for record in records)
    completion = sum(int(record.get("completion_tokens", 0)) for record in records)
    tooled = [record for record in records if record.get("tools")]
    lookups = sum(lookup_turns(record) for record in tooled)
    opened = sum(1 for record in tooled if lookup_turns(record) > 0)
    mean = f"{lookups / len(tooled):.2f}" if tooled else "-"
    return (
        f"{len(records)} runs ({by_stop}); {calls} model calls, "
        f"{prompt} prompt / {completion} completion tokens; "
        f"lookup turns {mean}/run over {len(tooled)} runs with tools "
        f"({opened} opened with a lookup)"
    )


def _history_line(history: dict[str, Any]) -> str:
    if "kept" in history:  # schema <= 3: the loop windowed turns
        return f"  history: kept {history.get('kept', 0)}, dropped {history.get('dropped', 0)}"
    recall = history.get("recall") or ""
    return f"  history: {history.get('given', 0)} message(s), recall {len(recall)} chars"


def detail(record: dict[str, Any]) -> str:
    serving = record.get("serving") or {}
    history = record.get("history") or {}
    limits = record.get("limits") or {}
    limit = record.get("limit")
    stop = str(record.get("stop_reason"))
    if limit:
        stop += f" (limit {limit}" + (f"={limits[limit]})" if limit in limits else ")")
    if record.get("summarised"):  # schema <= 3
        stop += ", reply is the limit summary"
    elif "narrated" in record and not record["narrated"] and record.get("reply") is not None:
        stop += ", reply is the planner's note (the narrator could not speak)"
    lines = [
        f"run {record.get('run_id')}  conv {record.get('conversation_id')}  "
        f"{record.get('actor')}  {record.get('ts')}",
        f"  ask:     {clip(record.get('utterance', ''), 300)!r}",
        _history_line(history),
        f"  lookups: {lookup_turns(record)} turn(s) of only reads before the first write",
        f"  stop:    {stop}",
    ]
    if record.get("error"):
        lines.append(f"  error:   {clip(record['error'], 300)}")
    reply = record.get("reply")
    lines.append(f"  reply:   {clip(reply, 300)!r}" if reply is not None else "  reply:   (none)")
    if record.get("note") is not None:
        lines.append(f"  note:    {clip(record['note'], 300)!r}")
    lines.append(
        f"  serving: {serving.get('model')} profile={serving.get('profile')} "
        f"prompt={str(serving.get('system_prompt_sha256'))[:12]} "
        f"tools={str(serving.get('tool_schemas_sha256'))[:12]} "
        f"({serving.get('tools_offered')} offered)"
    )
    gathered = record.get("gather")
    if gathered:
        found = ", ".join(
            f"#{m.get('id')}" + (f" lift {m['lift']:+.3f}" if m.get("lift") is not None else "")
            for m in gathered.get("matches") or []
        )
        lines.append(
            f"  gather:  {gathered.get('source')} {gathered.get('ms')}ms"
            f" -> {found or 'no matches'}"
        )
    if record.get("state_block"):
        lines.append("  state block:")
        lines.extend(f"    {line}" for line in str(record["state_block"]).splitlines())
    lines.append("  model calls:")
    for call in record.get("calls") or []:
        cached = call.get("cached_tokens")
        server_ms = call.get("server_ms")
        line = (
            f"    #{call.get('seq')} {call.get('phase'):<8} {call.get('status'):<9} "
            f"{call.get('ms')}ms"
            + (f" (server {server_ms}ms)" if server_ms is not None else "")
            + f"  prompt {call.get('prompt_tokens')}"
            + (f" (cached {cached})" if cached is not None else "")
            + f"  completion {call.get('completion_tokens')}"
            + f"  -> {call.get('tool_calls', 0)} tool calls"
        )
        if call.get("failure"):
            line += f"  [{clip(call['failure'], 120)}]"
        lines.append(line)
    lines.append("  tool calls:")
    tools = record.get("tools") or []
    if not tools:
        lines.append("    (none)")
    for index, tool in enumerate(tools, start=1):
        args = json.dumps(tool.get("args"), separators=(",", ":"), sort_keys=True)
        outcome = (
            f"-> {clip(tool['result'])}"
            if tool.get("result") is not None
            else f"!! {clip(tool.get('error'))}"
        )
        lines.append(f"    #{index} {tool.get('name')} {clip(args)} {outcome}")
    lines.append(
        f"  totals:  {record.get('model_calls', 0)} calls "
        f"({record.get('unmetered_calls', 0)} unmetered), "
        f"{record.get('model_ms', 0)}ms model of {record.get('total_ms', 0)}ms, "
        f"{record.get('prompt_tokens', 0)} prompt "
        f"({record.get('cached_tokens', 0)} cached) / "
        f"{record.get('completion_tokens', 0)} completion tokens"
    )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument("path", type=Path, help="the trace file (PCC_TRACE_PATH)")
    parser.add_argument("--run", help="show one run in full: a run id or a prefix of one")
    parser.add_argument("--stop", help="only runs that ended with this stop reason")
    parser.add_argument("--last", type=int, help="only the newest N runs")
    parser.add_argument("--json", action="store_true", help="print records as indented JSON")
    args = parser.parse_args(argv)

    if not args.path.exists():
        print(f"{args.path}: no such trace file", file=sys.stderr)
        return 1
    records = list(read_records(args.path))
    if args.run:
        records = [r for r in records if str(r.get("run_id", "")).startswith(args.run)]
        if not records:
            print(f"no run matching {args.run!r}", file=sys.stderr)
            return 1
    if args.stop:
        records = [r for r in records if r.get("stop_reason") == args.stop]
    if args.last is not None:
        records = records[-args.last :] if args.last > 0 else []

    if args.json:
        for record in records:
            print(json.dumps(record, indent=2, ensure_ascii=False))
    elif args.run:
        print("\n\n".join(detail(record) for record in records))
    else:
        for record in records:
            print(summary_line(record))
        print(totals(records))
    return 0


if __name__ == "__main__":
    sys.exit(main())
