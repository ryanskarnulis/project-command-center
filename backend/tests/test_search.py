import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.models import TaskWorkflowStatus
from app.services import projects as projects_service
from app.services import search as search_service
from app.services import task_trash
from app.services import tasks as tasks_service


def _seed(db: Session) -> None:
    project = projects_service.create_project(
        db, name="Firewall Upgrade", description="network hardening"
    )
    tasks_service.create_task(
        db, project_id=project.id, title="Audit firewall rules"
    )
    db.commit()


def test_search_matches_each_kind(db_session: Session) -> None:
    _seed(db_session)

    results = search_service.search(db_session, "firewall")

    assert [p.title for p in results.projects] == ["Firewall Upgrade"]
    assert [t.title for t in results.tasks] == ["Audit firewall rules"]
    # Task subtitle resolves to the owning project's name.
    assert results.tasks[0].subtitle == "Firewall Upgrade"
    assert results.tasks[0].project_id is not None


def test_search_tasks_carry_status_fields(db_session: Session) -> None:
    """Tasks expose workflow status (for /done); other kinds leave it None."""
    _seed(db_session)

    results = search_service.search(db_session, "firewall")

    task = results.tasks[0]
    assert task.workflow_status == "open"
    # The status field is task-only; projects serialize as null.
    assert results.projects[0].workflow_status is None


def test_search_is_case_insensitive(db_session: Session) -> None:
    _seed(db_session)

    results = search_service.search(db_session, "FIREWALL")

    assert len(results.projects) == 1
    assert len(results.tasks) == 1


def test_search_blank_query_returns_empty(db_session: Session) -> None:
    _seed(db_session)

    results = search_service.search(db_session, "   ")

    assert results.projects == []
    assert results.tasks == []


def test_search_excludes_soft_deleted(db_session: Session) -> None:
    project = projects_service.create_project(db_session, name="Firewall Upgrade")
    task = tasks_service.create_task(
        db_session, project_id=project.id, title="Audit firewall rules"
    )
    tasks_service.soft_delete_task(db_session, task)
    db_session.commit()

    results = search_service.search(db_session, "firewall")

    assert [t.title for t in results.tasks] == []
    assert [p.title for p in results.projects] == ["Firewall Upgrade"]


def test_search_escapes_like_wildcards(db_session: Session) -> None:
    # A bare "%" must not behave as a match-all wildcard.
    projects_service.create_project(db_session, name="Firewall Upgrade")
    projects_service.create_project(db_session, name="50% capacity plan")
    db_session.commit()

    # "%" is treated as a literal: it matches the row that actually contains a
    # percent sign, and must NOT match everything (which an unescaped wildcard would).
    wildcard = search_service.search(db_session, "%")
    literal = search_service.search(db_session, "50%")

    assert [p.title for p in wildcard.projects] == ["50% capacity plan"]
    assert [p.title for p in literal.projects] == ["50% capacity plan"]


def test_search_ranks_exact_title_above_newer_description_match(
    db_session: Session,
) -> None:
    """Relevance beats recency: an exact title wins over a newer description-only hit."""
    project = projects_service.create_project(db_session, name="P")
    # Older row matches the title exactly (best tier).
    tasks_service.create_task(db_session, project_id=project.id, title="firewall")
    # Newer row only matches on description; recency must not float it to the top.
    tasks_service.create_task(
        db_session,
        project_id=project.id,
        title="zzz top",
        description="firewall config",
    )
    db_session.commit()

    results = search_service.search(db_session, "firewall")

    assert [t.title for t in results.tasks] == ["firewall", "zzz top"]


def test_search_ranks_prefix_above_substring(db_session: Session) -> None:
    """A prefix match outranks a mid-string substring match, even when it's older."""
    project = projects_service.create_project(db_session, name="P")
    tasks_service.create_task(
        db_session, project_id=project.id, title="firewall audit"
    )  # prefix
    tasks_service.create_task(
        db_session, project_id=project.id, title="check the firewall now"
    )  # substring, newer
    db_session.commit()

    results = search_service.search(db_session, "firewall")

    assert [t.title for t in results.tasks] == [
        "firewall audit",
        "check the firewall now",
    ]


def test_search_prefers_open_over_done_at_same_text_tier(
    db_session: Session,
) -> None:
    """At the same text tier, an accepted+open task outranks a done one (and stays
    ahead even though the done task is newer)."""
    project = projects_service.create_project(db_session, name="P")
    tasks_service.create_task(
        db_session, project_id=project.id, title="firewall ready"
    )  # accepted + open
    tasks_service.create_task(
        db_session,
        project_id=project.id,
        title="firewall closed",
        workflow_status=TaskWorkflowStatus.done,
    )  # done, newer
    db_session.commit()

    results = search_service.search(db_session, "firewall")

    assert [t.title for t in results.tasks] == ["firewall ready", "firewall closed"]


def test_search_text_relevance_beats_task_state_bias(db_session: Session) -> None:
    """A done exact-title hit still outranks an open description-only hit."""
    project = projects_service.create_project(db_session, name="P")
    tasks_service.create_task(
        db_session,
        project_id=project.id,
        title="firewall",
        workflow_status=TaskWorkflowStatus.done,
    )
    tasks_service.create_task(
        db_session,
        project_id=project.id,
        title="zzz top",
        description="firewall config",
    )
    db_session.commit()

    results = search_service.search(db_session, "firewall")

    assert [t.title for t in results.tasks] == ["firewall", "zzz top"]


def test_search_reports_effective_done_for_completed_checklist_parent(
    db_session: Session,
) -> None:
    """A checklist parent whose children are all done reads as done in search.

    Its stored column stays open (status rolls up from children and is never
    written back), so search must resolve the effective status — otherwise the
    command bar keeps offering a finished checklist as a /done candidate.
    """
    project = projects_service.create_project(db_session, name="P")
    parent = tasks_service.create_task(
        db_session, project_id=project.id, title="firewall rollout"
    )
    tasks_service.create_task(
        db_session,
        project_id=project.id,
        title="child step",
        parent_task_id=parent.id,
        workflow_status=TaskWorkflowStatus.done,
    )
    db_session.commit()

    # Stored column is still open; effective status is done.
    assert parent.workflow_status == TaskWorkflowStatus.open
    results = search_service.search(db_session, "firewall rollout")
    match = next(t for t in results.tasks if t.id == parent.id)
    assert match.workflow_status == TaskWorkflowStatus.done


def test_search_reports_effective_open_for_reopened_leaf(
    db_session: Session,
) -> None:
    """A stored-done leaf that gains an active child reads as not-done in search.

    Its status now rolls up from the new child, so search must not keep serving
    the stale done column (which would hide it from the not-done command-bar set).
    """
    project = projects_service.create_project(db_session, name="P")
    leaf = tasks_service.create_task(
        db_session,
        project_id=project.id,
        title="firewall audit",
        workflow_status=TaskWorkflowStatus.done,
    )
    tasks_service.create_task(
        db_session,
        project_id=project.id,
        title="new child step",
        parent_task_id=leaf.id,
    )
    db_session.commit()

    assert leaf.workflow_status == TaskWorkflowStatus.done
    results = search_service.search(db_session, "firewall audit")
    match = next(t for t in results.tasks if t.id == leaf.id)
    assert match.workflow_status != TaskWorkflowStatus.done


def test_search_keeps_open_match_past_the_scan_cap(db_session: Session) -> None:
    """An open match survives a full scan cap's worth of newer done matches.

    The scan guard must not decide inclusion ahead of the state tiebreak: with
    ``_TASK_SCAN_CAP`` newer done tasks sharing the exact title, the older open
    one still has to reach the relevance sort (and lead it).
    """
    project = projects_service.create_project(db_session, name="P")
    open_task = tasks_service.create_task(
        db_session, project_id=project.id, title="shared search term"
    )
    for _ in range(search_service._TASK_SCAN_CAP):
        tasks_service.create_task(
            db_session,
            project_id=project.id,
            title="shared search term",
            workflow_status=TaskWorkflowStatus.done,
        )
    db_session.commit()

    results = search_service.search(db_session, "shared search term")

    assert open_task.id in [t.id for t in results.tasks]
    assert results.tasks[0].id == open_task.id


def test_search_route_happy_path(client: TestClient, db_session: Session) -> None:
    _seed(db_session)

    response = client.get("/api/search", params={"q": "firewall"})

    assert response.status_code == 200
    body = response.json()
    assert body["projects"][0]["kind"] == "project"
    assert body["tasks"][0]["kind"] == "task"


def test_search_route_blank_query(client: TestClient, db_session: Session) -> None:
    _seed(db_session)

    response = client.get("/api/search", params={"q": ""})

    assert response.status_code == 200
    assert response.json() == {"projects": [], "tasks": []}


# --- Word matching (#444) -------------------------------------------------
#
# Search matches a query by its words (any order, stop-words ignored, simple
# inflections folded by FTS5's porter stemmer), not as one whole-query substring.


def _garage(db: Session) -> None:
    project = projects_service.create_project(db, name="Garage")
    for title in ("Paint the walls", "Buy primer", "Sweep the floor"):
        tasks_service.create_task(db, project_id=project.id, title=title)
    db.commit()


@pytest.mark.parametrize(
    ("query", "expected_first"),
    [
        # The issue's table: names as stored still match ...
        ("Paint the walls", "Paint the walls"),
        ("paint", "Paint the walls"),
        ("primer", "Buy primer"),
        # ... and so do paraphrases that used to return nothing.
        ("Painting the walls", "Paint the walls"),
        ("buying primer", "Buy primer"),
        ("sweep floor", "Sweep the floor"),  # a stop-word missing
        ("floor sweep", "Sweep the floor"),  # word order
        ("the walls, painting", "Paint the walls"),
        ("PAINTED WALLS", "Paint the walls"),
        # Irregular inflection is beyond a stemmer, but the shared word still
        # surfaces the task (in the "some words" tier).
        ("floor swept", "Sweep the floor"),
    ],
)
def test_search_matches_by_words(db_session: Session, query: str, expected_first: str) -> None:
    _garage(db_session)

    results = search_service.search(db_session, query)

    assert results.tasks, f"{query!r} found nothing"
    assert results.tasks[0].title == expected_first


def test_search_ignores_stop_words(db_session: Session) -> None:
    """A stop-word shared with another title ("the") must not drag it in."""
    _garage(db_session)

    results = search_service.search(db_session, "painting the walls")

    assert [t.title for t in results.tasks] == ["Paint the walls"]


def test_search_stop_word_only_query_still_matches(db_session: Session) -> None:
    """A query made only of stop-words keeps them rather than matching nothing."""
    _garage(db_session)

    results = search_service.search(db_session, "the")

    assert {t.title for t in results.tasks} == {"Paint the walls", "Sweep the floor"}


def test_search_word_matching_covers_descriptions_and_projects(db_session: Session) -> None:
    project = projects_service.create_project(
        db_session, name="Garage renovation", description="walls and floors"
    )
    tasks_service.create_task(
        db_session,
        project_id=project.id,
        title="Errand",
        description="buy the primer before painting",
    )
    db_session.commit()

    tasks = search_service.search(db_session, "buying primer").tasks
    projects = search_service.search(db_session, "renovating the garage").projects
    by_description = search_service.search(db_session, "floor wall").projects

    assert [t.title for t in tasks] == ["Errand"]
    assert [p.title for p in projects] == ["Garage renovation"]
    assert [p.title for p in by_description] == ["Garage renovation"]


def test_search_ranks_exact_prefix_all_words_some_words(db_session: Session) -> None:
    """exact > prefix > all words > some words, regardless of recency.

    Created worst-first so every newer row is a better match: recency alone
    would produce exactly the reverse order.
    """
    project = projects_service.create_project(db_session, name="P")
    for title in (
        "Paint the fence",  # some words
        "Walls need painting",  # all words, any order, inflected
        "Paint walls today",  # prefix
        "paint walls",  # exact
    ):
        tasks_service.create_task(db_session, project_id=project.id, title=title)
    tasks_service.create_task(db_session, project_id=project.id, title="Unrelated")
    db_session.commit()

    results = search_service.search(db_session, "Paint walls")

    assert [t.title for t in results.tasks] == [
        "paint walls",
        "Paint walls today",
        "Walls need painting",
        "Paint the fence",
    ]


def test_search_some_words_ranks_more_matched_words_first(db_session: Session) -> None:
    project = projects_service.create_project(db_session, name="P")
    tasks_service.create_task(db_session, project_id=project.id, title="Buy paint brushes")
    tasks_service.create_task(db_session, project_id=project.id, title="Buy milk")  # newer
    db_session.commit()

    results = search_service.search(db_session, "buy paint rollers")

    assert [t.title for t in results.tasks] == ["Buy paint brushes", "Buy milk"]


def test_search_title_words_outrank_description_words(db_session: Session) -> None:
    project = projects_service.create_project(db_session, name="P")
    tasks_service.create_task(db_session, project_id=project.id, title="Walls need paint")
    tasks_service.create_task(
        db_session, project_id=project.id, title="Errand", description="paint walls"
    )  # newer, description only
    db_session.commit()

    results = search_service.search(db_session, "painting walls")

    assert [t.title for t in results.tasks] == ["Walls need paint", "Errand"]


def test_search_word_match_excludes_soft_deleted(db_session: Session) -> None:
    project = projects_service.create_project(db_session, name="Garage")
    task = tasks_service.create_task(db_session, project_id=project.id, title="Paint the walls")
    gone = projects_service.create_project(db_session, name="Old garage renovation")
    db_session.commit()
    tasks_service.soft_delete_task(db_session, task)
    projects_service.soft_delete_project(db_session, gone)
    db_session.commit()

    results = search_service.search(db_session, "painting walls garage renovating")

    assert results.tasks == []
    assert [p.title for p in results.projects] == ["Garage"]


def _fts_integrity_check(db: Session) -> None:
    """FTS5's own check that each index matches its content table (raises if not)."""
    for table in ("tasks_fts", "projects_fts"):
        db.execute(text(f"INSERT INTO {table}({table}, rank) VALUES ('integrity-check', 1)"))


def test_search_index_follows_renames(db_session: Session) -> None:
    project = projects_service.create_project(db_session, name="Garage")
    task = tasks_service.create_task(
        db_session, project_id=project.id, title="Paint the walls", description="white"
    )
    db_session.commit()

    tasks_service.update_task(
        db_session, task, {"title": "Sand the deck", "description": "coarse grit"}
    )
    projects_service.update_project(db_session, project, {"name": "Backyard"})
    db_session.commit()

    _fts_integrity_check(db_session)
    assert search_service.search(db_session, "painting walls").tasks == []
    assert search_service.search(db_session, "white").tasks == []
    assert [t.title for t in search_service.search(db_session, "sanding decks").tasks] == [
        "Sand the deck"
    ]
    assert [t.title for t in search_service.search(db_session, "grit").tasks] == [
        "Sand the deck"
    ]
    assert search_service.search(db_session, "garage").projects == []
    assert [p.title for p in search_service.search(db_session, "backyards").projects] == [
        "Backyard"
    ]


def test_search_index_follows_restore_and_purge(db_session: Session) -> None:
    project = projects_service.create_project(db_session, name="Garage")
    keep = tasks_service.create_task(db_session, project_id=project.id, title="Paint the walls")
    purge = tasks_service.create_task(db_session, project_id=project.id, title="Paint the door")
    db_session.commit()
    tasks_service.soft_delete_task(db_session, keep)
    tasks_service.soft_delete_task(db_session, purge)
    db_session.commit()

    task_trash.restore_task_subtree(db_session, keep)
    task_trash.purge_task(db_session, purge)
    db_session.commit()

    _fts_integrity_check(db_session)
    assert [t.title for t in search_service.search(db_session, "painting").tasks] == [
        "Paint the walls"
    ]


@pytest.mark.parametrize(
    "query",
    [
        '"',
        '""',
        "*",
        "-",
        "^",
        ":",
        "(",
        ")",
        "OR",
        "AND",
        "NOT",
        "NEAR",
        "NEAR(paint walls)",
        "paint OR",
        "AND paint",
        "paint NOT walls",
        "-paint",
        "paint*",
        '"paint',
        "title:paint",
        "{title description}: paint",
        "rank:1",
        "walls + floor",
        "paint\x00walls",
        "\U0001f3a8 walls",
    ],
)
def test_search_tolerates_query_syntax_characters(db_session: Session, query: str) -> None:
    """User text never reaches FTS5 as query syntax: it can't raise or inject."""
    _garage(db_session)

    results = search_service.search(db_session, query)

    if any(word in query.lower() for word in ("paint", "walls")):
        assert "Paint the walls" in [t.title for t in results.tasks]


def test_search_operator_words_are_plain_words(db_session: Session) -> None:
    """"NOT" / "NEAR" in a title are words to find, not FTS5 operators."""
    project = projects_service.create_project(db_session, name="P")
    tasks_service.create_task(db_session, project_id=project.id, title="Stay NEAR the phone")
    tasks_service.create_task(db_session, project_id=project.id, title="Paint walls")
    db_session.commit()

    results = search_service.search(db_session, "near phone")

    assert [t.title for t in results.tasks] == ["Stay NEAR the phone"]
