from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from app.ai import gateway
from app.main import app
from app.services import settings as settings_service


@pytest.fixture
def isolated_local(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[Path, None, None]:
    """Point profile overrides at a throwaway file so tests never touch the repo's."""
    local = tmp_path / "profiles.local.yaml"
    monkeypatch.setattr(gateway, "_LOCAL_PROFILES_PATH", local)
    gateway.reload_profiles()
    yield local
    gateway.reload_profiles()


@pytest.fixture
def isolated_prompts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[Path, None, None]:
    """Point the prompts dir at a throwaway copy so PUT doesn't clobber real prompts."""
    pdir = tmp_path / "prompts"
    pdir.mkdir()
    (pdir / "extract_tasks.md").write_text("original prompt\n")
    monkeypatch.setattr(gateway, "_PROMPTS_DIR", pdir)
    yield pdir


@pytest.fixture
def lan_client() -> Generator[TestClient, None, None]:
    with TestClient(app, client=("192.168.1.50", 50000)) as test_client:
        yield test_client


class TestProfiles:
    def test_list_profiles(self, client: TestClient, isolated_local: Path) -> None:
        resp = client.get("/api/settings/profiles")
        assert resp.status_code == 200
        names = {p["name"] for p in resp.json()}
        assert {"task_extraction", "project_matching", "summary"} <= names
        for p in resp.json():
            assert p["overridden_fields"] == []

    def test_patch_overrides_and_leaves_committed_file_untouched(
        self, client: TestClient, isolated_local: Path
    ) -> None:
        committed_before = gateway._PROFILES_PATH.read_bytes()

        resp = client.patch(
            "/api/settings/profiles/task_extraction", json={"temperature": 0.7}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["temperature"] == 0.7
        assert "temperature" in body["overridden_fields"]

        # Effective value persists on re-read.
        again = client.get("/api/settings/profiles")
        row = next(p for p in again.json() if p["name"] == "task_extraction")
        assert row["temperature"] == 0.7

        # Override landed in the local file; committed profiles.yaml is byte-identical.
        assert isolated_local.exists()
        local = yaml.safe_load(isolated_local.read_text())
        assert local["task_extraction"]["temperature"] == 0.7
        assert gateway._PROFILES_PATH.read_bytes() == committed_before

    def test_patch_unknown_profile_404(
        self, client: TestClient, isolated_local: Path
    ) -> None:
        resp = client.patch("/api/settings/profiles/nope", json={"temperature": 0.5})
        assert resp.status_code == 404

    def test_patch_out_of_range_temperature_422(
        self, client: TestClient, isolated_local: Path
    ) -> None:
        resp = client.patch(
            "/api/settings/profiles/task_extraction", json={"temperature": 5}
        )
        assert resp.status_code == 422

    def test_patch_unknown_field_422(
        self, client: TestClient, isolated_local: Path
    ) -> None:
        resp = client.patch(
            "/api/settings/profiles/task_extraction", json={"provider": "openai"}
        )
        assert resp.status_code == 422

    def test_lan_client_can_read_profiles(
        self, lan_client: TestClient, isolated_local: Path
    ) -> None:
        resp = lan_client.get("/api/settings/profiles")
        assert resp.status_code == 200

    def test_lan_client_cannot_patch_profile(
        self, lan_client: TestClient, isolated_local: Path
    ) -> None:
        resp = lan_client.patch(
            "/api/settings/profiles/task_extraction", json={"temperature": 0.7}
        )
        assert resp.status_code == 403
        assert resp.json()["detail"] == "settings writes are only allowed from localhost"
        assert not isolated_local.exists()


class TestPrompts:
    def test_list_prompts(self, client: TestClient, isolated_prompts: Path) -> None:
        resp = client.get("/api/settings/prompts")
        assert resp.status_code == 200
        names = {p["name"] for p in resp.json()}
        assert "extract_tasks.md" in names

    def test_put_prompt_roundtrips(
        self, client: TestClient, isolated_prompts: Path
    ) -> None:
        resp = client.put(
            "/api/settings/prompts/extract_tasks.md", json={"text": "new body\n"}
        )
        assert resp.status_code == 200
        assert resp.json()["text"] == "new body\n"

        got = client.get("/api/settings/prompts/extract_tasks.md")
        assert got.json()["text"] == "new body\n"
        assert (isolated_prompts / "extract_tasks.md").read_text() == "new body\n"

    def test_get_unknown_prompt_404(
        self, client: TestClient, isolated_prompts: Path
    ) -> None:
        assert client.get("/api/settings/prompts/missing.md").status_code == 404

    def test_put_unknown_prompt_404(
        self, client: TestClient, isolated_prompts: Path
    ) -> None:
        resp = client.put("/api/settings/prompts/missing.md", json={"text": "x"})
        assert resp.status_code == 404

    def test_put_path_traversal_404(
        self, client: TestClient, isolated_prompts: Path
    ) -> None:
        resp = client.put("/api/settings/prompts/..%2fsecret.md", json={"text": "x"})
        assert resp.status_code == 404

    def test_lan_client_can_read_prompts(
        self, lan_client: TestClient, isolated_prompts: Path
    ) -> None:
        list_resp = lan_client.get("/api/settings/prompts")
        get_resp = lan_client.get("/api/settings/prompts/extract_tasks.md")

        assert list_resp.status_code == 200
        assert get_resp.status_code == 200

    def test_lan_client_cannot_put_prompt(
        self, lan_client: TestClient, isolated_prompts: Path
    ) -> None:
        resp = lan_client.put(
            "/api/settings/prompts/extract_tasks.md", json={"text": "new body\n"}
        )
        assert resp.status_code == 403
        assert resp.json()["detail"] == "settings writes are only allowed from localhost"
        assert (isolated_prompts / "extract_tasks.md").read_text() == "original prompt\n"


class TestEvalRun:
    def test_run_eval_returns_structured_results(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fake_run() -> list[dict[str, Any]]:
            return [
                {"name": "case_a", "passed": True, "reason": ""},
                {"name": "case_b", "passed": False, "reason": "boom"},
            ]

        monkeypatch.setitem(settings_service._EVAL_SUITES, "summary", fake_run)

        resp = client.post("/api/settings/evals/summary/run")
        assert resp.status_code == 200
        body = resp.json()
        assert body["suite"] == "summary"
        assert body["total"] == 2
        assert body["passed"] == 1
        assert len(body["cases"]) == 2

    def test_run_unknown_suite_404(self, client: TestClient) -> None:
        assert client.post("/api/settings/evals/bogus/run").status_code == 404

    def test_lan_client_cannot_run_eval(
        self, lan_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        called = False

        def fake_run() -> list[dict[str, Any]]:
            nonlocal called
            called = True
            return [{"name": "case_a", "passed": True, "reason": ""}]

        monkeypatch.setitem(settings_service._EVAL_SUITES, "summary", fake_run)

        resp = lan_client.post("/api/settings/evals/summary/run")
        assert resp.status_code == 403
        assert resp.json()["detail"] == "settings writes are only allowed from localhost"
        assert called is False
