"""Transport-agnostic agent tool layer: registry + per-call runtime plumbing.

The single source of truth for PCC's agent tool surface. Consumed by two
peers: the MCP server (``app/mcp/server.py``) and the in-app agent loop
(``app/ai/loop.py``). See ``docs/agent-design.md``.
"""
