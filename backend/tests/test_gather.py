"""The pre-run gather (#415): the embeddings client, hybrid ranking, the
fallback to keywords, the vector cache, and what the loop shows the model.

The embeddings service is faked with ``httpx.MockTransport``: a text's vector
is built from the concepts its words name, so "dennis" means the same as
"dentist" (a misheard name keywords can't match) and everything shares one
common axis, which keeps cosines compressed the way the real model's are.
"""

from __future__ import annotations

import json
import math
from datetime import date, timedelta
from collections.abc import Generator
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.ai import embeddings, gather, state_block
from app.ai.embeddings import Embedder, EmbeddingsUnavailable
from app.ai.gather import Searcher, VectorCache
from app.ai.loop import AgentLoop
from app.services import focus_plan as focus_plan_service
from app.services import projects as projects_service
from app.services import task_dependencies as deps_service
from app.services import tasks as tasks_service
from app.tools import runtime
from tests.scripted_provider import ScriptedProvider, text_turn

# Concept axes (after the common axis 0) and the words that name each.
CONCEPTS: dict[str, tuple[str, ...]] = {
    "dentist": ("dentist", "dennis"),
    "appointment": ("appointment",),
    "car": ("car",),
    "groceries": ("groceries",),
    "leaves": ("leaves",),
    "report": ("report",),
    "gutters": ("gutters",),
    "plumber": ("plumber",),
}
AXES = list(CONCEPTS)


def concept_vector(text: str) -> list[float]:
    words = text.lower().replace("|", " ").replace(".", " ").split()
    vector = [1.0] + [
        1.0 if any(w in words for w in CONCEPTS[axis]) else 0.0 for axis in AXES
    ]
    norm = math.sqrt(sum(x * x for x in vector))
    return [x / norm for x in vector]


class FakeService:
    """An embeddings server: records each request; can go down or swap model."""

    def __init__(self) -> None:
        self.model = "embeddinggemma-2"
        self.down = False
        self.requests: list[list[str]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("refused", request=request)
        texts = json.loads(request.content)["input"]
        self.requests.append(texts)
        data = [
            {"index": i, "embedding": concept_vector(text)} for i, text in enumerate(texts)
        ]
        # Out of order on purpose: the client sorts by index.
        return httpx.Response(200, json={"model": self.model, "data": data[::-1]})

    def embedder(self) -> Embedder:
        client = httpx.Client(transport=httpx.MockTransport(self.handler))
        return Embedder("http://embeddings.test/v1", client=client)


@pytest.fixture
def tool_db(
    test_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> Generator[sessionmaker[Session], None, None]:
    factory: sessionmaker[Session] = sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine
    )
    monkeypatch.setattr(runtime, "session_factory", factory)
    yield factory


@pytest.fixture(autouse=True)
def no_background_warm(monkeypatch: pytest.MonkeyPatch) -> None:
    """A warm thread would outlive the test's database; tests warm by hand."""
    monkeypatch.setattr(Searcher, "warm_in_background", lambda self: None)


@pytest.fixture
def service() -> FakeService:
    return FakeService()


TITLES = (
    "Dentist appointment",
    "Car service appointment",
    "Buy groceries",
    "Rake the leaves",
    "Write the quarterly report",
    "Clean the gutters",
    "Call the plumber",
)


def _seed(factory: sessionmaker[Session]) -> dict[str, int]:
    with factory() as db:
        project = projects_service.create_project(db, name="Home")
        ids = {
            title: tasks_service.create_task(db, project_id=project.id, title=title).id
            for title in TITLES
        }
        db.commit()
    return ids


def _searcher(service: FakeService, **kwargs: Any) -> Searcher:
    return Searcher(service.embedder(), build="b1", **kwargs)


# --- The client ---------------------------------------------------------------


def test_the_client_adds_the_prefixes_the_model_expects() -> None:
    assert embeddings.query_text("dennis") == "task: search result | query: dennis"
    assert embeddings.document_text("Rake  the\nleaves") == "title: none | text: Rake the leaves"
    assert gather.task_document("Buy primer", "for the  walls") == (
        "title: none | text: Buy primer. for the walls"
    )


def test_the_client_sorts_by_index_and_names_the_model(service: FakeService) -> None:
    got = service.embedder().embed(["a car", "the leaves"], timeout=embeddings.GATHER_TIMEOUT)
    assert got.model == "embeddinggemma-2"
    assert got.vectors == [concept_vector("a car"), concept_vector("the leaves")]


@pytest.mark.parametrize(
    "respond",
    [
        lambda r: httpx.Response(500, text="boom"),
        lambda r: httpx.Response(200, text="not json"),
        lambda r: httpx.Response(200, json={"model": "m", "data": []}),
        lambda r: httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0]}]}),
    ],
    ids=["status", "not_json", "too_few", "no_model"],
)
def test_any_unusable_answer_is_unavailable(respond: Any) -> None:
    client = httpx.Client(transport=httpx.MockTransport(respond))
    with pytest.raises(EmbeddingsUnavailable):
        Embedder("http://x/v1", client=client).embed(["q"], timeout=embeddings.GATHER_TIMEOUT)


def test_a_refused_connection_is_unavailable(service: FakeService) -> None:
    service.down = True
    with pytest.raises(EmbeddingsUnavailable):
        service.embedder().embed(["q"], timeout=embeddings.GATHER_TIMEOUT)


# --- Ranking ------------------------------------------------------------------


def test_meaning_finds_a_misheard_name_keywords_cannot(
    tool_db: sessionmaker[Session], service: FakeService
) -> None:
    ids = _seed(tool_db)
    searcher = _searcher(service)
    assert searcher.warm()

    found = searcher.gather("push the dennis appointment to friday")

    assert found.source == "hybrid"
    assert found.model == "embeddinggemma-2"
    assert found.matches[0].item[1] == ids["Dentist appointment"]
    # The car service shares the keyword and some meaning: a candidate, second.
    assert [m.item[1] for m in found.matches] == [
        ids["Dentist appointment"],
        ids["Car service appointment"],
    ]
    # The query went out with the query prefix.
    assert service.requests[-1] == ["task: search result | query: push the dennis appointment to friday"]


def test_chit_chat_finds_nothing(tool_db: sessionmaker[Session], service: FakeService) -> None:
    _seed(tool_db)
    searcher = _searcher(service)
    searcher.warm()
    found = searcher.gather("hi there")
    assert found.source == "hybrid"
    assert found.matches == []


def test_with_no_service_configured_keywords_alone_still_match(
    tool_db: sessionmaker[Session],
) -> None:
    ids = _seed(tool_db)
    found = Searcher(None).gather("rake leaves")
    assert found.source == "keyword"
    assert [m.item[1] for m in found.matches] == [ids["Rake the leaves"]]


def test_a_stopped_service_costs_recall_not_the_run(
    tool_db: sessionmaker[Session], service: FakeService
) -> None:
    ids = _seed(tool_db)
    searcher = _searcher(service)
    searcher.warm()
    service.down = True

    found = searcher.gather("move the dentist appointment")

    assert found.source == "keyword"
    assert ids["Dentist appointment"] in [m.item[1] for m in found.matches]


def test_a_task_with_no_vector_yet_ranks_by_keyword(
    tool_db: sessionmaker[Session], service: FakeService
) -> None:
    _seed(tool_db)
    searcher = _searcher(service)
    searcher.warm()
    with tool_db() as db:
        new = tasks_service.create_task(db, project_id=1, title="Fix the gate latch").id
        db.commit()

    found = searcher.gather("fix the gate latch")

    assert found.source == "hybrid"
    assert [m.item[1] for m in found.matches] == [new]
    assert found.matches[0].lift is None


def test_trashed_tasks_are_never_matches(
    tool_db: sessionmaker[Session], service: FakeService
) -> None:
    ids = _seed(tool_db)
    with tool_db() as db:
        task = tasks_service.get_task(db, ids["Dentist appointment"])
        assert task is not None
        tasks_service.soft_delete_task(db, task)
        db.commit()
    searcher = _searcher(service)
    searcher.warm()

    for each in (searcher, Searcher(None)):
        found = each.gather("the dentist appointment")
        assert ids["Dentist appointment"] not in [m.item[1] for m in found.matches]


def test_a_focus_commitment_is_a_match_beside_a_look_alike_task(
    tool_db: sessionmaker[Session],
) -> None:
    """"Skip today's team standup": the repeating commitment, by its entry id,
    not only the recurring task that also says "standup"."""
    today = date.today()
    with tool_db() as db:
        work = projects_service.create_project(db, name="Work")
        tasks_service.create_task(db, project_id=work.id, title="Prepare standup notes")
        standup = focus_plan_service.add_commitment(
            db,
            plan_date=today - timedelta(days=7),
            title="Team standup",
            start_minute=9 * 60 + 30,
            duration_minutes=15,
            repeat_interval={"unit": "day", "every": 1},
        )
        gone = focus_plan_service.add_commitment(
            db, plan_date=today - timedelta(days=1), title="Old standup",
            start_minute=600, duration_minutes=15,
        )
        db.commit()
        standup_id, gone_id = standup.id, gone.id

    found = Searcher(None).gather("Skip today's team standup, it's cancelled.")

    assert found.matches[0].item == ("commitment", standup_id)
    assert ("commitment", gone_id) not in [m.item for m in found.matches]
    shown = gather.rows(found)
    assert shown[0] == {
        "entry_id": standup_id,
        "title": "Team standup",
        "focus_commitment": True,
        "at": "09:30",
        "repeats": {"unit": "day", "every": 1},
    }
    assert shown[1]["title"] == "Prepare standup notes"


def test_an_empty_ask_gathers_nothing(tool_db: sessionmaker[Session]) -> None:
    assert Searcher(None).gather("   ").source == "none"


def test_a_broken_gather_is_no_matches_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> Session:
        raise RuntimeError("db gone")

    monkeypatch.setattr(runtime, "session_factory", boom)
    found = Searcher(None).gather("anything")
    assert found.matches == [] and found.source == "none"


# --- The vector cache -----------------------------------------------------------


def test_warm_embeds_only_what_changed_and_drops_what_is_gone(
    tool_db: sessionmaker[Session], service: FakeService, tmp_path: Path
) -> None:
    ids = _seed(tool_db)
    cache_path = tmp_path / "vectors.json"
    searcher = _searcher(service, cache_path=cache_path)
    assert searcher.warm()
    assert sum(len(batch) for batch in service.requests) == len(TITLES)

    with tool_db() as db:
        leaves = tasks_service.get_task(db, ids["Rake the leaves"])
        gutters = tasks_service.get_task(db, ids["Clean the gutters"])
        assert leaves is not None and gutters is not None
        tasks_service.update_task(db, leaves, {"title": "Rake the front leaves"})
        tasks_service.soft_delete_task(db, gutters)
        db.commit()
    service.requests.clear()
    assert searcher.warm()

    assert service.requests == [["title: none | text: Rake the front leaves"]]
    saved = json.loads(cache_path.read_text())
    assert saved["model"] == "embeddinggemma-2" and saved["build"] == "b1"
    assert len(saved["vectors"]) == len(TITLES) - 1

    # A new process starts from the file: nothing to embed.
    service.requests.clear()
    assert _searcher(service, cache_path=cache_path).warm()
    assert service.requests == []


def test_a_cache_from_another_build_or_unreadable_starts_empty(tmp_path: Path) -> None:
    path = tmp_path / "vectors.json"
    VectorCache("m", "b1", {"k": [1.0]}).save(path)
    assert VectorCache.load(path, "b1").vectors == {"k": [1.0]}
    assert VectorCache.load(path, "b2").vectors == {}
    path.write_text("{not json")
    assert VectorCache.load(path, "b1").vectors == {}
    assert VectorCache.load(tmp_path / "missing.json", "b1").vectors == {}


def test_a_new_model_on_the_service_drops_every_vector(
    tool_db: sessionmaker[Session], service: FakeService
) -> None:
    _seed(tool_db)
    searcher = _searcher(service)
    searcher.warm()
    service.model = "embeddinggemma-3"

    assert searcher.gather("the dennis appointment").source == "keyword"
    service.requests.clear()
    assert searcher.warm()
    assert sum(len(batch) for batch in service.requests) == len(TITLES)
    assert searcher.gather("the dennis appointment").source == "hybrid"


# --- What the model sees --------------------------------------------------------


def test_the_section_is_the_task_rows_under_the_label(
    tool_db: sessionmaker[Session], service: FakeService
) -> None:
    ids = _seed(tool_db)
    searcher = _searcher(service)
    searcher.warm()

    shown = gather.rows(searcher.gather("the dennis appointment"))
    text = gather.section(shown)

    assert text is not None
    lines = text.splitlines()
    assert lines[0] == gather.LABEL
    assert "nothing was done" in lines[0]
    first = json.loads(lines[1].removeprefix("- "))
    assert first == {
        "task_id": ids["Dentist appointment"],
        "title": "Dentist appointment",
        "project": "Home",
        "status": "open",
        "priority": "medium",
    }
    assert gather.rows(gather.Gathered()) == []
    assert gather.section([]) is None


def test_a_waiting_task_carries_its_edges_and_keeps_the_other_matches(
    tool_db: sessionmaker[Session],
) -> None:
    """One edge used to drop every match (#478): the titles lookup handed
    ``dict()`` a Result, which it read as a mapping."""
    ids = _seed(tool_db)
    report, plumber = ids["Write the quarterly report"], ids["Call the plumber"]
    with tool_db() as db:
        edge = deps_service.add_dependency(db, task_id=report, depends_on_id=plumber)
        db.commit()
        edge_id = edge.id
    gathered = gather.Gathered(
        matches=[
            gather.Match(item=("task", report), score=1.0),
            gather.Match(item=("task", plumber), score=0.9),
        ]
    )

    shown = gather.rows(gathered)

    assert [row["task_id"] for row in shown] == [report, plumber]
    assert shown[0]["waits_on"] == [
        {"dependency_id": edge_id, "task_id": plumber, "title": "Call the plumber"}
    ]
    assert "waits_on" not in shown[1]


def test_with_state_puts_the_matches_between_the_block_and_the_ask() -> None:
    assert state_block.with_state("B", "ask", "M") == "B\n\nM\n\nask"
    assert state_block.with_state("B", "ask", None) == "B\n\nask"


class _Recorder:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def write(self, record: dict[str, Any]) -> None:
        self.records.append(record)


def test_the_loop_shows_the_matches_once_and_traces_them(
    tool_db: sessionmaker[Session], service: FakeService
) -> None:
    ids = _seed(tool_db)
    searcher = _searcher(service)
    searcher.warm()
    provider = ScriptedProvider([text_turn("Done.")])
    tracer = _Recorder()

    result = AgentLoop(provider, tracer=tracer, searcher=searcher).run(
        "push the dennis appointment to friday"
    )

    assert result.reply == "Done."
    turn = provider.requests[0]["messages"][-1]["content"]
    assert gather.LABEL in turn
    assert turn.index("[End of state]") < turn.index(gather.LABEL)
    assert turn.endswith("push the dennis appointment to friday")
    assert result.gathered[0]["title"] == "Dentist appointment"
    traced = tracer.records[0]["gather"]
    assert traced["rows"] == result.gathered
    assert traced["source"] == "hybrid"
    assert traced["matches"][0]["id"] == ids["Dentist appointment"]
    assert traced["matches"][0]["lift"] is not None


def test_no_matches_leaves_the_turn_as_it_was(tool_db: sessionmaker[Session]) -> None:
    provider = ScriptedProvider([text_turn("Hello!")])
    AgentLoop(provider, tracer=_Recorder(), searcher=Searcher(None)).run("hi there")
    turn = provider.requests[0]["messages"][-1]["content"]
    assert gather.LABEL not in turn
    assert turn.endswith("[End of state]\n\nhi there")
