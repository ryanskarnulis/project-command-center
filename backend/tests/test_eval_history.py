from collections.abc import Sequence
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.services import eval_history
from app.services import settings as settings_service


def test_record_and_list_runs(db_session: Session) -> None:
    eval_history.record_run(db_session, suite="summary", passed=2, total=3)
    eval_history.record_run(db_session, suite="summary", passed=3, total=3)
    eval_history.record_run(db_session, suite="task_extraction", passed=1, total=1)
    db_session.commit()

    summary_runs = eval_history.list_runs(db_session, suite="summary")
    assert len(summary_runs) == 2
    # Newest-first.
    assert summary_runs[0].passed == 3
    assert summary_runs[1].passed == 2

    all_runs = eval_history.list_runs(db_session)
    assert len(all_runs) == 3


def test_run_eval_route_persists_a_row(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run() -> list[dict[str, Any]]:
        return [
            {"name": "case_a", "passed": True, "reason": ""},
            {"name": "case_b", "passed": False, "reason": "boom"},
        ]

    monkeypatch.setitem(settings_service._EVAL_SUITES, "summary", fake_run)

    resp = client.post("/api/settings/evals/summary/run")
    assert resp.status_code == 200

    runs: Sequence[Any] = eval_history.list_runs(db_session, suite="summary")
    assert len(runs) == 1
    assert runs[0].passed == 1
    assert runs[0].total == 2

    history = client.get("/api/settings/evals/runs?suite=summary").json()
    assert len(history) == 1
    assert history[0]["passed"] == 1
    assert history[0]["total"] == 2
    assert "created_at" in history[0]
