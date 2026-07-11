"""AI provider layer (Phase 2): clients for the local model runtime.

The agent loop (next checkout) consumes ``providers.llamacpp``; nothing in
the HTTP app imports this package yet. Fresh module — the pre-strip ``ai/``
package this path once held is gone, not revived.
"""
