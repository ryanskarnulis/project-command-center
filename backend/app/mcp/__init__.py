"""PCC MCP server: the service layer exposed as MCP tools (Phase 2).

Design: ``docs/agent-design.md``. The server is a peer of the UI — every
mutation goes through ``app/services/``, is validated at the boundary, lands
in ``activity_events`` stamped ``actor="agent:mcp"``, and is undoable via the
trash. The purge/empty-trash services are deliberately never registered as
tools, so no MCP client can hard-delete anything.
"""
