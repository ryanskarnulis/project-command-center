"""``POST /api/trash/purge`` id lists are bounded at the boundary (#264).

``purge_selected`` expands ``task_ids`` / ``project_ids`` into SQL ``IN (...)``
lists, one bound parameter per id. Nothing capped the list length, so a request
carrying more ids than SQLite's 32,766-parameter ceiling reached the driver and
died as an unhandled ``OperationalError: too many SQL variables`` — a raw 500 on
a LAN-reachable endpoint. ``MAX_PURGE_IDS`` now rejects those at the schema, the
same boundary-rejection shape as #182 (out-of-range ids) and #235 (oversized
offsets).

The cap is far above anything reachable through the UI (/trash pages at most 200
rows per kind), so these tests pin both halves: oversized is a 422, and a list
right at the cap still executes as ordinary SQL.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.schemas.trash import MAX_PURGE_IDS, PurgeSelectedRequest

# SQLite's compiled-in ``SQLITE_MAX_VARIABLE_NUMBER`` since 3.32. The cap has to
# stay under it with room to spare, or the 500 this issue is about comes back.
SQLITE_MAX_VARIABLES = 32_766


def _ids(count: int) -> list[int]:
    return list(range(1, count + 1))


def _purge(client: TestClient, **payload: list[int]) -> httpx.Response:
    return client.post("/api/trash/purge", json=payload)


def test_cap_leaves_headroom_under_the_sqlite_ceiling() -> None:
    """The whole point of the cap: a legal request can never overrun SQLite."""
    assert MAX_PURGE_IDS < SQLITE_MAX_VARIABLES


# --- Oversized lists are rejected --------------------------------------------


@pytest.mark.parametrize("field", ["task_ids", "project_ids"])
def test_purge_rejects_oversized_id_list(client: TestClient, field: str) -> None:
    """One past the cap is a documented 422, not an OperationalError 500."""
    response = _purge(client, **{field: _ids(MAX_PURGE_IDS + 1)})

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert any(entry["type"] == "too_long" for entry in detail), detail
    assert any(field in entry["loc"] for entry in detail), detail


def test_purge_rejects_list_past_the_sqlite_ceiling(client: TestClient) -> None:
    """The size from the issue report — 40,000 ids used to be a 500."""
    response = _purge(client, task_ids=_ids(40_000), project_ids=[])

    assert response.status_code == 422, response.status_code


@pytest.mark.parametrize("field", ["task_ids", "project_ids"])
def test_oversized_list_fails_model_validation(field: str) -> None:
    """Rejected at the schema, so every caller of the model inherits the bound."""
    with pytest.raises(ValidationError) as exc:
        PurgeSelectedRequest.model_validate({field: _ids(MAX_PURGE_IDS + 1)})
    assert any(error["type"] == "too_long" for error in exc.value.errors())


# --- Legal requests are unaffected -------------------------------------------


def test_purge_accepts_both_lists_at_the_cap(client: TestClient) -> None:
    """A request right at the cap binds real SQL and answers normally.

    None of the ids are in trash, so the purge is a no-op — the assertion is that
    the id-resolution reads execute at all at this size.
    """
    response = _purge(
        client, task_ids=_ids(MAX_PURGE_IDS), project_ids=_ids(MAX_PURGE_IDS)
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"projects": 0, "tasks": 0}


def test_purge_still_removes_a_normal_selection(client: TestClient) -> None:
    """Happy path: an ordinary selection purges exactly what it names."""
    project = client.post("/api/projects", json={"name": "Bounded"}).json()
    task = client.post(
        f"/api/projects/{project['id']}/tasks", json={"title": "purge me"}
    ).json()
    assert client.delete(f"/api/tasks/{task['id']}").status_code == 204
    assert client.delete(f"/api/projects/{project['id']}").status_code == 204

    response = _purge(
        client, task_ids=[task["id"]], project_ids=[project["id"]]
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"projects": 1, "tasks": 1}
    trash = client.get("/api/trash").json()
    assert trash["projects"] == [] and trash["tasks"] == []


def test_purge_still_accepts_empty_lists(client: TestClient) -> None:
    """A ``max_length`` bound must not have introduced a minimum."""
    response = _purge(client, task_ids=[], project_ids=[])

    assert response.status_code == 200, response.text
    assert response.json() == {"projects": 0, "tasks": 0}


def test_purge_still_rejects_out_of_range_element(client: TestClient) -> None:
    """The per-element ``EntityId`` bound survives the list-level one (#182)."""
    response = _purge(client, task_ids=[2**63], project_ids=[])

    assert response.status_code == 422, response.text
