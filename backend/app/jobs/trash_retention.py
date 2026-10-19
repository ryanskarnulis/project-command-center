"""Scheduled trash retention: purge trash older than the configured window (#321).

The one sanctioned scheduled hard-delete path. It is a plain asyncio task
started in ``app.main``'s lifespan, not a scheduler library or a worker queue:
the deployment runs a single uvicorn worker (the same reasoning as
``app/api/conversation_locks.py``), so exactly one loop runs per database — and
a pass is idempotent anyway, so a second copy would find nothing left to purge.

The MCP server is a separate process that never runs this lifespan, and no
agent tool reaches ``purge_expired``: the agent still cannot hard-delete.

Every pass goes through ``trash.purge_expired`` → ``purge_selected``, so it
records the same per-row ``purged`` activity events as a manual purge,
attributed to the ``retention`` actor.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import structlog

from app.config import get_settings
from app.db.session import write_session
from app.services import trash as trash_service
from app.services.activity import current_actor

logger = structlog.get_logger(__name__)

# Activity-log actor for rows the job purges (None is the user, "agent:*" the agent).
RETENTION_ACTOR = "retention"

# First pass shortly after startup rather than inside it: startup stays fast, and
# a deploy's ``alembic upgrade`` + boot has settled before the job takes the
# write lock.
FIRST_RUN_DELAY_SECONDS = 60.0

# Then every six hours. The window is counted in days and the UI's countdown
# counts a part day as a day ("1 day left" = gone within a day), so a row is
# never purged early; six hours bounds how long an expired row lingers past its
# window (the UI says "Removed today" meanwhile), and a pass that finds nothing
# costs one indexed SELECT per kind. A constant, not config: nothing about a
# deployment changes the right answer.
INTERVAL_SECONDS = 6 * 60 * 60

# Tests switch this off (conftest) so a TestClient's lifespan never starts the
# loop, which would otherwise reach for the application database.
enabled = True


def run_once(now: datetime | None = None) -> trash_service.PurgeCounts:
    """One retention pass in its own ``BEGIN IMMEDIATE`` transaction.

    Synchronous: the loop runs it via ``asyncio.to_thread``. The write lock is
    taken at the first statement, so a restore can't land between choosing the
    expired rows and purging them (pysqlite would otherwise defer ``BEGIN`` past
    the SELECTs).
    """
    retention_days = get_settings().trash_retention_days
    if retention_days <= 0:
        return trash_service.PurgeCounts(projects=0, tasks=0)
    token = current_actor.set(RETENTION_ACTOR)
    try:
        with write_session() as db:
            counts = trash_service.purge_expired(
                db, now=now or datetime.now(UTC), retention_days=retention_days
            )
            db.commit()
    finally:
        current_actor.reset(token)
    logger.info(
        "trash_retention_purged",
        projects=counts.projects,
        tasks=counts.tasks,
        retention_days=retention_days,
    )
    return counts


async def retention_loop(
    run: Callable[[], object] = run_once,
    *,
    first_delay: float = FIRST_RUN_DELAY_SECONDS,
    interval: float = INTERVAL_SECONDS,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Run ``run`` after ``first_delay``, then every ``interval``, until cancelled.

    A failing pass is logged and the loop carries on to the next one: a locked
    database or one bad row must not stop retention for the life of the process,
    let alone take the app down. Cancellation (shutdown) propagates.
    """
    await sleep(first_delay)
    while True:
        try:
            await asyncio.to_thread(run)
        except Exception:
            logger.exception("trash_retention_failed")
        await sleep(interval)


def start() -> asyncio.Task[None] | None:
    """Start the loop on the running event loop; ``None`` when it shouldn't run.

    Not started for tests (``enabled``) or when ``TRASH_RETENTION_DAYS=0``;
    ``run_once`` re-reads the setting on every pass as well, so a 0 never purges.
    """
    if not enabled:
        return None
    retention_days = get_settings().trash_retention_days
    if retention_days <= 0:
        logger.info("trash_retention_disabled")
        return None
    logger.info(
        "trash_retention_scheduled",
        retention_days=retention_days,
        first_run_in_seconds=FIRST_RUN_DELAY_SECONDS,
        interval_seconds=INTERVAL_SECONDS,
    )
    return asyncio.create_task(retention_loop(), name="trash-retention")


async def stop(task: asyncio.Task[None] | None) -> None:
    """Cancel the loop and wait for it to unwind (shutdown)."""
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
