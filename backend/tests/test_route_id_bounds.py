"""Ints that reach SQL are bounded at the boundary (#182, #235).

A digit-only path segment used to parse as an unbounded Python ``int``, reach
``Task.id == task_id`` / ``Project.id == project_id``, and fail during SQL
binding as a 500 — SQLite's INTEGER is signed 64-bit. Every DB-backed id param
is now an ``EntityId``, so out-of-range values are a 422 before any SQL runs.

Pagination offsets had the same hole one layer up (#235): ``ge=0`` rejected
negatives but nothing rejected an offset above SQLite's range, so it reached
``.offset(...)`` and raised ``OverflowError``. They are now ``PaginationOffset``
at both boundaries that accept one — the HTTP route and the agent tool.
"""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.schemas.common import MAX_SQLITE_INT
from app.tools import registry

# One past SQLite's signed 64-bit maximum, plus the value from the issue report.
SIGNED_64_OVERFLOW = str(2**63)
FAR_OVERFLOW = "999999999999999999999999"
# Exactly representable in Python and SQLite, but not in a JS double.
JS_UNSAFE = "9007199254740993"

OUT_OF_RANGE = ["0", SIGNED_64_OVERFLOW, FAR_OVERFLOW]


def _project_id(client: TestClient) -> int:
    created = client.post("/api/projects", json={"name": "Bounds"})
    assert created.status_code == 201
    return int(created.json()["id"])


def test_task_route_rejects_out_of_range_ids(client: TestClient) -> None:
    for value in OUT_OF_RANGE:
        response = client.get(f"/api/tasks/{value}")
        assert response.status_code == 422, (value, response.text)


def test_project_route_rejects_out_of_range_ids(client: TestClient) -> None:
    for value in OUT_OF_RANGE:
        response = client.get(f"/api/projects/{value}")
        assert response.status_code == 422, (value, response.text)


def test_js_unsafe_id_is_in_range_and_merely_missing(client: TestClient) -> None:
    """A JS-unsafe but storage-valid id is a normal 404, not a 500.

    The frontend refuses to build a URL for it (``isValidRouteId``), but a direct
    request must still answer like any other unknown id.
    """
    assert client.get(f"/api/tasks/{JS_UNSAFE}").status_code == 404
    assert client.get(f"/api/projects/{JS_UNSAFE}").status_code == 404


def test_unknown_in_range_id_still_404s(client: TestClient) -> None:
    assert client.get("/api/tasks/987654").status_code == 404
    assert client.get("/api/projects/987654").status_code == 404


def test_malformed_route_still_not_found(client: TestClient) -> None:
    """Non-numeric segments keep the #107 behavior (no route match → 404)."""
    assert client.get("/api/tasks/nope").status_code == 422
    assert client.get("/api/nope/1").status_code == 404


def test_valid_ids_are_unaffected(client: TestClient) -> None:
    project_id = _project_id(client)
    assert client.get(f"/api/projects/{project_id}").status_code == 200
    created = client.post(
        f"/api/projects/{project_id}/tasks", json={"title": "in range"}
    )
    assert created.status_code == 201
    task_id = created.json()["id"]
    assert client.get(f"/api/tasks/{task_id}").status_code == 200


def test_out_of_range_id_in_payload_rejected(client: TestClient) -> None:
    """ID-bearing request payload fields are bounded too, not just path params."""
    response = client.post(
        "/api/tasks", json={"title": "bad parent", "parent_task_id": FAR_OVERFLOW}
    )
    assert response.status_code == 422, response.text


def test_write_routes_reject_out_of_range_ids(client: TestClient) -> None:
    assert client.patch(f"/api/tasks/{FAR_OVERFLOW}", json={"title": "x"}).status_code == 422
    assert client.delete(f"/api/projects/{FAR_OVERFLOW}").status_code == 422
    assert client.post(f"/api/tasks/{FAR_OVERFLOW}/done").status_code == 422


# --- Pagination offsets (#235) ------------------------------------------------


def _conversations(client: TestClient, offset: str) -> int:
    return client.get(
        "/api/agent/conversations", params={"limit": 1, "offset": offset}
    ).status_code


def test_max_in_range_offset_pages_past_the_end(client: TestClient) -> None:
    """SQLite's maximum is a bindable offset: an empty page, not an error."""
    response = client.get(
        "/api/agent/conversations", params={"limit": 1, "offset": str(MAX_SQLITE_INT)}
    )
    assert response.status_code == 200, response.text
    assert response.json() == []


def test_conversation_offset_rejects_out_of_range(client: TestClient) -> None:
    """Above SQLite's range is a 422 at the boundary, not an OverflowError 500."""
    assert _conversations(client, SIGNED_64_OVERFLOW) == 422
    assert _conversations(client, FAR_OVERFLOW) == 422


def test_conversation_offset_still_rejects_negative(client: TestClient) -> None:
    assert _conversations(client, "-1") == 422


def test_conversation_offset_and_limit_still_page(client: TestClient) -> None:
    for title in ("first", "second"):
        assert client.post("/api/agent/conversations", json={"title": title}).status_code == 201
    first_page = client.get("/api/agent/conversations", params={"limit": 1, "offset": 0})
    second_page = client.get("/api/agent/conversations", params={"limit": 1, "offset": 1})
    assert first_page.status_code == 200 and second_page.status_code == 200
    assert len(first_page.json()) == len(second_page.json()) == 1
    assert first_page.json()[0]["id"] != second_page.json()[0]["id"]


@pytest.mark.parametrize("offset", [2**63, -1])
def test_agent_tool_offset_rejects_out_of_range(offset: int) -> None:
    """The agent's ``list_tasks`` offset reaches the same ``.offset(...)`` call.

    Validation is the tool's arg model, so a bad model-authored offset is a
    self-correctable validation error instead of an OverflowError mid-run.
    """
    arg_model = registry.get_tool("list_tasks").metadata.arg_model
    with pytest.raises(ValidationError):
        arg_model.model_validate({"offset": offset})


def test_agent_tool_offset_accepts_max_in_range() -> None:
    arg_model = registry.get_tool("list_tasks").metadata.arg_model
    validated = arg_model.model_validate({"offset": MAX_SQLITE_INT})
    assert validated.model_dump()["offset"] == MAX_SQLITE_INT
