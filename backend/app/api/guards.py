from __future__ import annotations

from collections.abc import Callable
from ipaddress import ip_address
from typing import TypeVar

from fastapi import HTTPException, Request, status

from app.api.request_ip import direct_peer, is_trusted_proxy, proxy_is_host_only

RowT = TypeVar("RowT")


def require_local_write(request: Request) -> None:
    """Allow sensitive mutations only from the host.

    Reserved for sensitive routes (e.g. future agent endpoints): with
    ``API_HOST=0.0.0.0`` the API is reachable from the whole LAN with no auth,
    so host-only mutations need an explicit guard. Two callers are allowed:

    1. A direct loopback peer — a direct-bind dev server, or a ``docker exec``
       client inside the backend container.
    2. A request forwarded by the trusted reverse proxy *when the dashboard is
       bound host-only* (``proxy_is_host_only``). In that mode the LAN cannot
       reach the proxy, so every forwarded request is from the host. We do not
       trust ``X-Forwarded-For`` to look like loopback — the leftmost entries are
       client-forgeable — so exposing the dashboard on the LAN
       (``FRONTEND_BIND=0.0.0.0``) automatically re-denies proxied writes.
    """
    peer = direct_peer(request)
    if peer in {"localhost", "testclient"}:
        return
    if peer is not None:
        try:
            if ip_address(peer).is_loopback:
                return
        except ValueError:
            pass

    if is_trusted_proxy(peer) and proxy_is_host_only():
        return

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="this operation is only allowed from localhost",
    )


def trashed_row_or_error(
    trashed: RowT | None,
    active_lookup: Callable[[], object | None],
    *,
    conflict_detail: str,
    absent_detail: str,
) -> RowT:
    """Resolve a purge target, distinguishing "not trashed yet" from "gone".

    Purge only ever touches rows already in trash. When the trashed lookup comes
    back empty, an *active* row with the same id means the caller skipped the
    soft-delete step (409, ``conflict_detail``); no row at all is a plain 404
    (``absent_detail``). ``active_lookup`` is a callable so the second query only
    runs on the error path.
    """
    if trashed is not None:
        return trashed
    if active_lookup() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=conflict_detail
        )
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=absent_detail)
