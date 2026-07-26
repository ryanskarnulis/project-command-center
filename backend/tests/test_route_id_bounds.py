"""Route ids are bounded at the API boundary (#182).

A digit-only path segment used to parse as an unbounded Python ``int``, reach
``Task.id == task_id`` / ``Project.id == project_id``, and fail during SQL
binding as a 500 — SQLite's INTEGER is signed 64-bit. Every DB-backed id param
is now an ``EntityId``, so out-of-range values are a 422 before any SQL runs,
consistent with the other bounded params (``limit``, ``offset``).
"""

from fastapi.testclient import TestClient

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
