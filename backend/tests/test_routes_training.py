from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.services import training_data as training_service
from app.services.common import soft_delete


def _seed(db: Session) -> None:
    """Two task_extraction rows (one accepted), one match row, one soft-deleted."""
    training_service.record_example(
        db,
        task_name="task_extraction",
        input_text="finish firewall cleanup by Friday",
        model_output_json='{"tasks": []}',
        corrected_output_json='{"tasks": [{"title": "Firewall cleanup"}]}',
        accepted=True,
        model_profile="task_extraction",
        model_name="gemma4:e2b",
    )
    training_service.record_example(
        db,
        task_name="task_extraction",
        input_text="random note",
        model_output_json='{"tasks": []}',
        accepted=False,
        model_profile="task_extraction",
        model_name="gemma4:e2b",
    )
    training_service.record_example(
        db,
        task_name="project_matching",
        input_text="the vpn thing",
        model_output_json='{"project_id": 3}',
        accepted=True,
        model_profile="project_matching",
        model_name="gemma4:e2b",
    )
    deleted = training_service.record_example(
        db,
        task_name="task_extraction",
        input_text="should not be counted",
        model_output_json="{}",
        model_profile="task_extraction",
        model_name="gemma4:e2b",
    )
    soft_delete(deleted)
    db.commit()


def test_stats_excludes_soft_deleted(client: TestClient, db_session: Session) -> None:
    _seed(db_session)

    resp = client.get("/api/training-examples/stats")
    assert resp.status_code == 200
    body = resp.json()
    # 3 active rows (the soft-deleted one is excluded), 2 accepted.
    assert body["total"] == 3
    assert body["accepted"] == 2
    assert body["by_task"] == {"task_extraction": 2, "project_matching": 1}
    assert body["goal"] == 200
    assert body["remaining"] == 197


def test_list_returns_full_triples(client: TestClient, db_session: Session) -> None:
    _seed(db_session)

    resp = client.get("/api/training-examples")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 3  # soft-deleted excluded
    first = rows[0]
    # Full input + output + corrected output present (newest-first, so this is
    # the project_matching row).
    assert {"input_text", "model_output_json", "corrected_output_json"} <= first.keys()


def test_list_filters_by_task_and_accepted(
    client: TestClient, db_session: Session
) -> None:
    _seed(db_session)

    by_task = client.get("/api/training-examples?task_name=task_extraction").json()
    assert len(by_task) == 2
    assert all(r["task_name"] == "task_extraction" for r in by_task)

    accepted = client.get("/api/training-examples?accepted=true").json()
    assert len(accepted) == 2
    assert all(r["accepted"] for r in accepted)


def test_list_pagination(client: TestClient, db_session: Session) -> None:
    _seed(db_session)

    page = client.get("/api/training-examples?limit=1&offset=1").json()
    assert len(page) == 1
