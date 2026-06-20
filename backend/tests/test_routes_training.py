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
    # Per-task counts now carry the accepted share (one of the two
    # task_extraction rows is accepted; the project_matching row is accepted).
    assert body["by_task"] == {
        "task_extraction": {"count": 2, "accepted": 1},
        "project_matching": {"count": 1, "accepted": 1},
    }
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


def test_list_filters_by_search(client: TestClient, db_session: Session) -> None:
    _seed(db_session)

    # Matches input_text, case-insensitively.
    firewall = client.get("/api/training-examples?search=firewall").json()
    assert len(firewall) == 1
    assert firewall[0]["input_text"] == "finish firewall cleanup by Friday"

    upper = client.get("/api/training-examples?search=FIREWALL").json()
    assert [r["id"] for r in upper] == [r["id"] for r in firewall]

    # Matches model_output_json (the project_matching row's output).
    by_output = client.get("/api/training-examples?search=project_id").json()
    assert len(by_output) == 1
    assert by_output[0]["task_name"] == "project_matching"


def test_list_pagination(client: TestClient, db_session: Session) -> None:
    _seed(db_session)

    page = client.get("/api/training-examples?limit=1&offset=1").json()
    assert len(page) == 1


def _one_example(db: Session) -> int:
    """Persist a single active example and return its id."""
    example = training_service.record_example(
        db,
        task_name="task_extraction",
        input_text="junk note to prune",
        model_output_json='{"tasks": []}',
        accepted=False,
        model_profile="task_extraction",
        model_name="gemma4:e2b",
    )
    db.commit()
    return example.id


def test_delete_moves_example_to_trash_and_drops_from_corpus(
    client: TestClient, db_session: Session
) -> None:
    example_id = _one_example(db_session)
    assert client.get("/api/training-examples/stats").json()["total"] == 1

    resp = client.delete(f"/api/training-examples/{example_id}")
    assert resp.status_code == 204

    # Gone from the corpus list and the stats total (it's trashed, not counted).
    assert client.get("/api/training-examples").json() == []
    assert client.get("/api/training-examples/stats").json()["total"] == 0

    # Deleting it again 404s (no active row with that id anymore).
    assert client.delete(f"/api/training-examples/{example_id}").status_code == 404


def test_restore_returns_example_to_corpus(
    client: TestClient, db_session: Session
) -> None:
    example_id = _one_example(db_session)
    client.delete(f"/api/training-examples/{example_id}")

    restored = client.post(f"/api/training-examples/{example_id}/restore")
    assert restored.status_code == 200
    assert restored.json()["id"] == example_id

    listed = client.get("/api/training-examples").json()
    assert [r["id"] for r in listed] == [example_id]
    assert client.get("/api/training-examples/stats").json()["total"] == 1


def test_purge_requires_trashed_row(
    client: TestClient, db_session: Session
) -> None:
    example_id = _one_example(db_session)

    # Active (not trashed) → 409: it must be deleted first.
    assert client.delete(f"/api/training-examples/{example_id}/purge").status_code == 409

    client.delete(f"/api/training-examples/{example_id}")
    purged = client.delete(f"/api/training-examples/{example_id}/purge")
    assert purged.status_code == 204

    # The row is truly gone now — a second purge 404s.
    from app.db.models import AITrainingExample

    assert db_session.get(AITrainingExample, example_id) is None
    assert client.delete(f"/api/training-examples/{example_id}/purge").status_code == 404


def test_trashed_example_appears_in_trash_and_empties(
    client: TestClient, db_session: Session
) -> None:
    example_id = _one_example(db_session)
    client.delete(f"/api/training-examples/{example_id}")

    trash = client.get("/api/trash").json()
    assert [e["id"] for e in trash["training_examples"]] == [example_id]
    assert client.get("/api/trash/count").json()["training_examples"] == 1

    result = client.delete("/api/trash").json()
    assert result["training_examples"] == 1
    assert client.get("/api/trash").json()["training_examples"] == []
