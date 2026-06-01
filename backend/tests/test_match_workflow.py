import json

import pytest
from sqlalchemy.orm import Session

from app.ai import gateway
from app.ai.workflows import match_project as workflow
from app.db.models import AITrainingExample, InboxItem
from app.services import projects as projects_service
from app.services.common import active


def _inbox(db: Session, *, hint: str | None) -> InboxItem:
    item = InboxItem(raw_text="notes", input_hash="hash-" + (hint or "none"))
    item.project_hint = hint
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def _no_model(**_: object) -> str:
    raise AssertionError("the model must not be called on this path")


def test_match_deterministic_alias_makes_no_model_call(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gateway, "complete", _no_model)
    project = projects_service.create_project(db_session, name="Home Network")
    projects_service.create_alias(db_session, project_id=project.id, alias="firewall")

    item = _inbox(db_session, hint="firewall ruleset cleanup")
    matched = workflow.match_inbox_item(db_session, item)

    assert matched is not None and matched.id == project.id
    db_session.refresh(item)
    assert item.suggested_project_id == project.id
    assert item.match_output_json is None  # deterministic: no model output


def test_match_deterministic_alias_in_raw_text_without_hint(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Regression: the extractor often returns no project_hint, but the alias is
    # right there in the note. Matching must search the raw text, not just the hint.
    monkeypatch.setattr(gateway, "complete", _no_model)
    project = projects_service.create_project(db_session, name="Home Network")
    projects_service.create_alias(db_session, project_id=project.id, alias="firewall")

    item = InboxItem(raw_text="finish the firewall cleanup", input_hash="h1")
    item.project_hint = None  # extractor surfaced no hint
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)

    matched = workflow.match_inbox_item(db_session, item)
    assert matched is not None and matched.id == project.id
    db_session.refresh(item)
    assert item.suggested_project_id == project.id


def test_match_ai_fallback_sets_suggestion(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = projects_service.create_project(db_session, name="Home Network")
    item = _inbox(db_session, hint="the home net thing")  # no deterministic hit

    raw = json.dumps({"project_id": project.id, "confidence": 0.8, "reasoning": "fit"})
    monkeypatch.setattr(gateway, "complete", lambda **_: raw)

    matched = workflow.match_inbox_item(db_session, item)

    assert matched is not None and matched.id == project.id
    db_session.refresh(item)
    assert item.suggested_project_id == project.id
    assert item.match_output_json == raw
    assert item.match_model_name == gateway.get_profile("project_matching").model
    assert item.match_input_text is not None and "Home Network" in item.match_input_text


def test_match_ai_out_of_range_id_leaves_unmatched(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_service.create_project(db_session, name="Home Network")
    item = _inbox(db_session, hint="the home net thing")

    raw = json.dumps({"project_id": 9999, "confidence": 0.9})  # id not offered
    monkeypatch.setattr(gateway, "complete", lambda **_: raw)

    matched = workflow.match_inbox_item(db_session, item)

    assert matched is None
    db_session.refresh(item)
    assert item.suggested_project_id is None
    assert item.match_output_json == raw
    # The non-offered id is captured as a training failure case.
    examples = db_session.execute(active(AITrainingExample)).scalars().all()
    assert [e.task_name for e in examples] == ["project_matching"]
    assert examples[0].accepted is False


def test_match_ai_validation_failure_is_nonfatal_and_recorded(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_service.create_project(db_session, name="Home Network")
    item = _inbox(db_session, hint="the home net thing")

    bad = "this is not json"
    monkeypatch.setattr(gateway, "complete", lambda **_: bad)

    matched = workflow.match_inbox_item(db_session, item)  # must not raise

    assert matched is None
    db_session.refresh(item)
    assert item.suggested_project_id is None
    assert item.match_output_json == bad
    examples = db_session.execute(active(AITrainingExample)).scalars().all()
    assert len(examples) == 1 and examples[0].task_name == "project_matching"


def test_match_is_idempotent(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = projects_service.create_project(db_session, name="Home Network")
    item = _inbox(db_session, hint="the home net thing")

    calls = 0

    def fake_complete(**_: object) -> str:
        nonlocal calls
        calls += 1
        return json.dumps({"project_id": project.id, "confidence": 0.8})

    monkeypatch.setattr(gateway, "complete", fake_complete)

    first = workflow.match_inbox_item(db_session, item)
    second = workflow.match_inbox_item(db_session, item)

    assert calls == 1  # second run must not call the model again
    assert first is not None and second is not None
    assert first.id == second.id == project.id


def test_match_no_hint_makes_no_call(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gateway, "complete", _no_model)
    projects_service.create_project(db_session, name="Home Network")
    item = _inbox(db_session, hint=None)

    assert workflow.match_inbox_item(db_session, item) is None
    db_session.refresh(item)
    assert item.suggested_project_id is None
    assert item.match_output_json is None


def test_match_no_projects_makes_no_call(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gateway, "complete", _no_model)
    item = _inbox(db_session, hint="firewall stuff")  # hint, but no projects exist

    assert workflow.match_inbox_item(db_session, item) is None
    db_session.refresh(item)
    assert item.suggested_project_id is None
    assert item.match_output_json is None
