"""Transaction-mode guarantees: who takes the SQLite write lock, and when.

pysqlite's legacy mode emits ``BEGIN`` only before DML and never before a
``SELECT``, so a read-then-write sequence was not one transaction and every
check-then-act guard in the service layer could act on stale reads. ``BEGIN
IMMEDIATE`` on writers closes that: the loser's transaction starts only after the
winner commits, so its guard sees the winner's row.

These tests pin the mechanism. The races the mechanism prevents are in
``test_concurrency.py``.
"""

from __future__ import annotations

import pytest
from fastapi.routing import APIRoute
from sqlalchemy import Engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import begin_immediate
from app.main import app
from app.tools import runtime

READ_METHODS = {"GET", "HEAD", "OPTIONS"}


def _dependency_names(route: APIRoute) -> set[str]:
    return {
        dep.call.__name__
        for dep in route.dependant.dependencies
        if dep.call is not None
    } | {
        sub.call.__name__
        for dep in route.dependant.dependencies
        for sub in dep.dependencies
        if sub.call is not None
    }


class TestWriteRoutesTakeTheWriteLock:
    def test_every_write_route_depends_on_get_db_write(self) -> None:
        """A mutating route on ``get_db`` is a silent race, not a visible bug.

        Enumerated from the live app rather than a hand-kept list, so a new POST
        added on the read session fails here instead of in production.
        """
        offenders: list[str] = []
        for route in app.routes:
            if not isinstance(route, APIRoute):
                continue
            if not (route.methods - READ_METHODS):
                continue
            names = _dependency_names(route)
            if "get_db" in names and "get_db_write" not in names:
                offenders.append(f"{sorted(route.methods)} {route.path}")
        assert not offenders, (
            "these mutating routes use the read session and can race:\n  "
            + "\n  ".join(offenders)
        )

    def test_read_routes_do_not_take_the_write_lock(self) -> None:
        """Reads must stay DEFERRED — a GET holding the write lock serializes writers."""
        offenders = [
            f"{sorted(route.methods)} {route.path}"
            for route in app.routes
            if isinstance(route, APIRoute)
            and not (route.methods - READ_METHODS)
            and "get_db_write" in _dependency_names(route)
        ]
        assert not offenders, "read-only routes on the write session:\n  " + "\n  ".join(
            offenders
        )


def _record_begins(engine: Engine, sink: list[str]) -> None:
    def capture(
        _conn: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if statement.startswith("BEGIN"):
            sink.append(statement)

    event.listen(engine, "before_cursor_execute", capture)


class TestBeginStatements:
    """The listener has to actually emit what each mode claims.

    Exercised against ``file_engine`` rather than by calling ``get_db_write``
    directly: those dependencies bind the application engine, which points at the
    real database file.
    """

    def test_a_plain_read_session_begins_deferred(
        self, file_engine: Engine, session_factory: sessionmaker[Session]
    ) -> None:
        seen: list[str] = []
        _record_begins(file_engine, seen)
        db = session_factory()
        db.execute(text("SELECT 1"))
        db.commit()
        db.close()
        assert seen == ["BEGIN"], seen

    def test_begin_statement_on_the_connection_wins(self, file_engine: Engine) -> None:
        """The mechanism ``get_db_write`` uses: set it on the Connection it owns."""
        seen: list[str] = []
        _record_begins(file_engine, seen)
        conn = file_engine.connect()
        conn.info["begin_statement"] = "BEGIN IMMEDIATE"
        db = sessionmaker(bind=conn)()
        db.execute(text("SELECT 1"))
        db.commit()
        db.close()
        conn.close()
        assert seen == ["BEGIN IMMEDIATE"], seen

    def test_begin_immediate_contextvar_drives_the_statement(
        self, file_engine: Engine, session_factory: sessionmaker[Session]
    ) -> None:
        """The mechanism the MCP/agent tool session uses: it owns no Connection."""
        seen: list[str] = []
        _record_begins(file_engine, seen)

        for flag in (True, False):
            token = begin_immediate.set(flag)
            try:
                db = session_factory()
                db.execute(text("SELECT 1"))
                db.commit()
                db.close()
            finally:
                begin_immediate.reset(token)

        assert seen == ["BEGIN IMMEDIATE", "BEGIN"], seen


class TestToolSessionParity:
    """The MCP server is a separate process; it needs the same lock discipline."""

    def test_write_tool_session_begins_immediate(
        self,
        file_engine: Engine,
        session_factory: sessionmaker[Session],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(runtime, "session_factory", session_factory)
        seen: list[str] = []
        _record_begins(file_engine, seen)
        with runtime.tool_session("create_task") as db:
            db.execute(text("SELECT 1"))
        assert seen == ["BEGIN IMMEDIATE"], seen

    def test_read_tool_session_begins_deferred(
        self,
        file_engine: Engine,
        session_factory: sessionmaker[Session],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(runtime, "session_factory", session_factory)
        seen: list[str] = []
        _record_begins(file_engine, seen)
        with runtime.tool_session("list_tasks", write=False) as db:
            db.execute(text("SELECT 1"))
        assert seen == ["BEGIN"], seen
