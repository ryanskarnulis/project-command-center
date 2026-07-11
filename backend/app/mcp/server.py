"""PCC MCP server (stdio): the shared tool registry over the MCP protocol.

Run with ``python -m app.mcp.server`` from ``backend/`` (the repo's
``.mcp.json`` does exactly that). The tools themselves live in
``app/tools/registry.py``, shared with the in-app agent loop; this module is
only transport wiring — a FastMCP instance with every registry tool
registered. FastMCP re-derives each tool's schema from the same signatures
and docstrings the registry uses, so what an MCP client sees is exactly what
the loop's provider sees.
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

for _registered in registry.all_tools():
    mcp.add_tool(_registered.fn)


def main() -> None:
    # stdout carries the JSON-RPC transport; all logging must go to stderr.
    configure_logging(stream=sys.stderr)
    logger.info("mcp_server_starting", server="pcc", tools=len(registry.all_tools()))
    mcp.run()  # stdio transport


if __name__ == "__main__":
    main()
