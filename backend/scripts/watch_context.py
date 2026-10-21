#!/usr/bin/env python3
"""Follow every model call's exact context while the agent runs (#401).

    python backend/scripts/watch_context.py CONTEXT.jsonl [--trace AGENT.jsonl]
        [--phase turn] [--json] [--from-start]

Tails the file ``PCC_CONTEXT_PATH`` writes (``app/ai/context_capture.py``;
layout in ``docs/agent-context-capture.md``) and prints each model call as it
lands, grouped under its run: the prompt the server's chat template rendered —
the text the model tokenized — then the raw response body, thought block and
tool-call text included. With ``--trace`` pointing at the ``PCC_TRACE_PATH``
file, the run's outcome follows its calls once the run record is written: the
ask, every tool call and its answer, the stop reason, and the reply.

Captured content is printed exactly as captured — no wrapping, re-indenting or
pretty-printing. Separators and headers are the only additions, and the
outcome block (read from the trace, already a summary) is the only formatted
part. Standard library only: it needs no venv and does not import the app.

``--phase`` follows one phase (``turn`` or ``summary``); ``--json`` prints the
request body instead of the rendered prompt; ``--from-start`` replays the
files from the top instead of waiting for new lines.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, TextIO

# Mirrors of ``context_capture.KIND_MODEL_CALL`` and ``trace.KIND_RUN``, kept as
# literals so the watcher runs from any checkout with nothing but the files.
KIND_MODEL_CALL = "model_call"
KIND_RUN = "run"
SOURCE_CONTEXT = "context"
SOURCE_TRACE = "trace"

_POLL_S = 0.25
_CLIP = 300


class Watcher:
    """Turns captured lines into terminal output. Pure: ``feed`` a line from
    either file and it writes to ``out`` — so the grouping and the join are
    tested without a file, a clock or a GPU."""

    def __init__(self, out: TextIO, *, phase: str | None = None, show_json: bool = False) -> None:
        self._out = out
        self._phase = phase
        self._show_json = show_json
        self._group: object = object()

    def feed(self, line: str, source: str) -> None:
        if not line.strip():
            return
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            self._write(f"!! unreadable {source} line skipped\n")
            return
        if not isinstance(record, dict):
            return
        kind = record.get("kind")
        if source == SOURCE_CONTEXT and kind == KIND_MODEL_CALL:
            if self._phase is None or record.get("phase") == self._phase:
                self._header(record.get("run_id"))
                self._write(render_call(record, show_json=self._show_json))
        elif source == SOURCE_TRACE and kind == KIND_RUN:
            self._header(record.get("run_id"))
            self._write(render_run(record))

    def _header(self, run_id: Any) -> None:
        if run_id == self._group:
            return
        self._group = run_id
        self._write(render_header(run_id))

    def _write(self, text: str) -> None:
        self._out.write(text)
        self._out.flush()


def render_header(run_id: str | None) -> str:
    title = "outside a run" if run_id is None else f"run {run_id}"
    return f"\n{'═' * 4} {title} {'═' * 4}\n"


def render_call(record: dict[str, Any], *, show_json: bool = False) -> str:
    """One call: a separator, then the captured text exactly as captured."""
    parts = [
        str(record.get("phase", "unknown")),
        f"seq {record.get('seq')}",
        f"{record.get('ms')} ms",
    ]
    status = record.get("status_code")
    parts.append("no response" if status is None else f"HTTP {status}")
    lines = [f"── {' · '.join(parts)} ──\n"]

    template = record.get("template") or {}
    prompt = template.get("prompt")
    if show_json or not isinstance(prompt, str):
        if not show_json:
            reason = template.get("error") or "not captured"
            lines.append(f"▸ prompt unavailable ({reason}); request JSON instead\n")
        lines.append("▸ request\n")
        lines.append(_verbatim(record.get("request")))
    else:
        lines.append("▸ prompt\n")
        lines.append(_verbatim(prompt))

    if record.get("error"):
        lines.append(f"▸ error: {record['error']}\n")
    if record.get("response") is not None:
        lines.append("▸ response\n")
        lines.append(_verbatim(record["response"]))
    return "".join(lines)


def render_run(record: dict[str, Any]) -> str:
    """The run's outcome, from its trace record."""
    lines = ["── outcome ──\n"]
    if record.get("utterance"):
        lines.append(f"utterance: {record['utterance']}\n")
    stop = record.get("stop_reason", "?")
    limit = record.get("limit")
    lines.append(f"stop: {stop}" + (f" ({limit})" if limit else "") + "\n")
    for tool in record.get("tools") or []:
        args = json.dumps(tool.get("args"), ensure_ascii=False)
        answer = tool.get("error") or tool.get("result")
        answer_text = answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)
        lines.append(f"tool {tool.get('name')} {args} → {_clip(answer_text)}\n")
    if record.get("error"):
        lines.append(f"error: {record['error']}\n")
    reply = record.get("reply")
    summarised = " (limit summary)" if record.get("summarised") else ""
    lines.append(f"reply{summarised}: {reply if reply is not None else '—'}\n")
    return "".join(lines)


def _clip(text: str) -> str:
    text = text.replace("\n", " ")
    return text if len(text) <= _CLIP else text[: _CLIP - 1] + "…"


def _verbatim(text: Any) -> str:
    # Exactly as captured; only a missing final newline is supplied, so the
    # next separator starts on its own line.
    text = "" if text is None else str(text)
    return text if text.endswith("\n") else text + "\n"


class Tail:
    """New complete lines of a file, from wherever reading started.

    A file that does not exist yet is waited for; one that shrank (truncated,
    or replaced) is read again from the top. A line still being written is
    left for the next read.
    """

    def __init__(self, path: Path, *, from_start: bool) -> None:
        self.path = path
        self._offset = 0 if from_start else _size(path)
        self._partial = b""

    def read(self) -> list[str]:
        size = _size(self.path)
        if size < self._offset:
            self._offset, self._partial = 0, b""
        if size == self._offset:
            return []
        with self.path.open("rb") as handle:
            handle.seek(self._offset)
            chunk = handle.read()
        self._offset += len(chunk)
        data = self._partial + chunk
        *complete, self._partial = data.split(b"\n")
        return [line.decode("utf-8", errors="replace") for line in complete]


def _written_at(line: str) -> str:
    """When a line's record was written: a capture's ``ended_at``, a run
    record's ``ts`` — both ISO-8601 UTC, so they compare as strings. A line
    without one sorts first, and the sort is stable, so each file's own order
    always holds."""
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        return ""
    if not isinstance(record, dict):
        return ""
    return str(record.get("ended_at") or record.get("ts") or "")


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except FileNotFoundError:
        return 0


def main(argv: list[str] | None = None, out: TextIO = sys.stdout) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("context", type=Path, help="the PCC_CONTEXT_PATH file")
    parser.add_argument("--trace", type=Path, help="the PCC_TRACE_PATH file")
    parser.add_argument("--phase", help="follow one phase only (turn or summary)")
    parser.add_argument(
        "--json", action="store_true", help="print the request JSON, not the prompt"
    )
    parser.add_argument("--from-start", action="store_true", help="replay the files from the top")
    parser.add_argument("--once", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    watcher = Watcher(out, phase=args.phase, show_json=args.json)
    tails = [(Tail(args.context, from_start=args.from_start), SOURCE_CONTEXT)]
    if args.trace is not None:
        tails.append((Tail(args.trace, from_start=args.from_start), SOURCE_TRACE))
    try:
        while True:
            # Merged by when each record was written, so a replay (or a poll
            # that caught both files moving) puts a run's outcome after its
            # own calls rather than after every call in the file.
            batch = [(line, source) for tail, source in tails for line in tail.read()]
            for line, source in sorted(batch, key=lambda item: _written_at(item[0])):
                watcher.feed(line, source)
            if args.once:
                return 0
            time.sleep(_POLL_S)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
