from __future__ import annotations

from collections.abc import Callable
from ipaddress import ip_address
from typing import TypeVar

from fastapi import HTTPException, Request, status

RowT = TypeVar("RowT")


def require_local_write(request: Request) -> None:
    """Allow sensitive mutations only from direct loopback/test clients.

    Guards the routes that change configuration (settings/prompt writes) or
    destroy data irreversibly (purge, empty-trash): with ``API_HOST=0.0.0.0`` the
    API is reachable from the whole LAN with no auth, and these are the only
    operations that can't be undone from the UI. This trusts
    ``request.client.host`` for direct binds. Reverse-proxy deployments need
    explicit trusted-proxy handling before forwarding these writes.
    """
    host = request.client.host if request.client else None
    if host in {"localhost", "testclient"}:
        return
    if host is not None:
        try:
            if ip_address(host).is_loopback:
                return
        except ValueError:
            pass

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
