"""PCC MCP server (stdio): the shared tool registry over the MCP protocol.

Run with ``python -m app.mcp.server`` from ``backend/`` (the repo's
``.mcp.json`` does exactly that). The tools themselves live in
``app/tools/registry.py``, shared with the in-app agent loop; this module is
only transport wiring — a FastMCP instance with every registry tool
registered with the registry's own argument model and description, so what
an MCP client sees is exactly what the loop's provider sees.
"""

from __future__ import annotations

import sys

import structlog
from mcp.server.fastmcp import FastMCP

from app.logging_config import configure_logging
from app.tools import registry

logger = structlog.get_logger(__name__)

mcp = FastMCP(
    "pcc",
    instructions=(
        "Project Command Center (PCC): local project and task management. "
        "All deletes are soft deletes into a trash (restorable via the "
        "restore_* tools); there is no permanent delete. Every write is "
        "recorded in the activity log attributed to this agent."
    ),
)

for _registered in registry.mcp_tools():
    # The registry's description (``inspect.getdoc``, cleaned), not FastMCP's
    # raw ``__doc__``: Python 3.13+ dedents docstrings at compile time and
    # 3.11 doesn't, so the raw form would differ by interpreter (#402).
    _tool = mcp._tool_manager.add_tool(_registered.fn, description=_registered.description)
    # And the registry's argument model, not one re-derived from the signature:
    # the payload tools advertise top-level arguments their signature doesn't
    # have (``app/tools/flat_args.py``, #411). FastMCP has no public hook for
    # this; the schema snapshot test pins MCP and the loop to the same schema.
    _tool.fn_metadata = _registered.metadata
    _tool.parameters = _registered.parameters


def main() -> None:
    # stdout carries the JSON-RPC transport; all logging must go to stderr.
    configure_logging(stream=sys.stderr)
    logger.info("mcp_server_starting", server="pcc", tools=len(registry.mcp_tools()))
    mcp.run()  # stdio transport


if __name__ == "__main__":
    main()
