"""The emitted tool schemas, pinned byte for byte (#402).

Two surfaces read the shared registry's schemas: the in-app agent, which gets
``registry.tool_specs()`` on every turn exactly as
``ToolSpec.to_wire()`` puts it on the wire, and the MCP server, whose
``tools/list`` answer is what an external agent reads. Both are serialized
into ``fixtures/tool_schemas.json`` — emitted key order kept, non-ASCII kept,
nothing normalized — so a new parameter, a renamed field, a reworded
docstring, a Pydantic upgrade that moves a ``title`` or a ``default`` shows up
as a fixture diff. A diff there is a tool-schema change, which runs the eval
gate (``docs/agent-design.md``, "Pinned bytes"). Regenerate deliberately::

    cd backend && PCC_UPDATE_FIXTURES=1 .venv/bin/pytest tests/test_tool_schema_snapshot.py
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from app.mcp.server import mcp
from app.tools import registry

FIXTURE = Path(__file__).parent / "fixtures" / "tool_schemas.json"


def _emitted() -> dict[str, list[dict[str, Any]]]:
    return {
        "agent": [spec.to_wire() for spec in registry.tool_specs()],
        "mcp": [
            tool.model_dump(mode="json", by_alias=True, exclude_none=True)
            for tool in asyncio.run(mcp.list_tools())
        ],
    }


def _serialized(emitted: dict[str, list[dict[str, Any]]]) -> str:
    """The fixture's one serialization: key order as emitted (no
    ``sort_keys``), non-ASCII kept, a trailing newline."""
    return json.dumps(emitted, indent=2, ensure_ascii=False) + "\n"


def _first_difference(expected: str, actual: str) -> str:
    for number, (want, got) in enumerate(
        zip(expected.splitlines(), actual.splitlines(), strict=False), start=1
    ):
        if want != got:
            return f"line {number}:\n  pinned:  {want!r}\n  emitted: {got!r}"
    return (
        f"lengths differ: pinned {len(expected.splitlines())} lines, "
        f"emitted {len(actual.splitlines())}"
    )


def test_emitted_tool_schemas_match_the_fixture_byte_for_byte() -> None:
    actual = _serialized(_emitted())
    if os.environ.get("PCC_UPDATE_FIXTURES") == "1":
        FIXTURE.parent.mkdir(exist_ok=True)
        FIXTURE.write_text(actual, encoding="utf-8")
    expected = FIXTURE.read_text(encoding="utf-8")
    assert actual == expected, (
        "the emitted tool schemas drifted from tests/fixtures/tool_schemas.json — "
        "every key reaches a model verbatim. If the change is deliberate, "
        "regenerate with PCC_UPDATE_FIXTURES=1, read the fixture diff, and run "
        "the eval gate. First difference at " + _first_difference(expected, actual)
    )


def test_the_fixture_is_the_serialization_this_test_would_write() -> None:
    """A hand edit that reorders keys, escapes a character or drops the
    trailing newline is a fixture problem, caught apart from a live drift."""
    text = FIXTURE.read_text(encoding="utf-8")
    assert _serialized(json.loads(text)) == text


def test_the_fixture_holds_every_registry_tool_on_both_surfaces() -> None:
    pinned = json.loads(FIXTURE.read_text(encoding="utf-8"))
    names = [tool.name for tool in registry.all_tools()]
    assert [tool["function"]["name"] for tool in pinned["agent"]] == names
    mcp_names = [tool.name for tool in registry.mcp_tools()]
    assert sorted(tool["name"] for tool in pinned["mcp"]) == sorted(mcp_names)


def test_mcp_descriptions_are_the_registry_s_cleaned_docstrings() -> None:
    """Both surfaces carry the ``inspect.getdoc`` form, which is the same on
    every interpreter (3.13+ dedents ``__doc__`` at compile time, 3.11 does
    not), so the fixture holds one set of bytes for CI and the image alike."""
    emitted = _emitted()
    agent = {tool["function"]["name"]: tool["function"]["description"] for tool in emitted["agent"]}
    mcp_side = {tool["name"]: tool["description"] for tool in emitted["mcp"]}
    assert mcp_side == {name: agent[name] for name in mcp_side}
