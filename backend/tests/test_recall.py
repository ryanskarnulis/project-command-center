"""What came before the run, as data for each phase (#418, ``app/ai/recall.py``)."""

from __future__ import annotations

from typing import Any

from app.ai import recall
from app.ai.loop import ToolCallRecord


def _assistant(content: str | None, *calls: ToolCallRecord) -> dict[str, Any]:
    turn: dict[str, Any] = {"role": "assistant", "content": content}
    if calls:
        turn["tool_calls"] = [call.model_dump() for call in calls]
    return turn


def _user(content: str) -> dict[str, Any]:
    return {"role": "user", "content": content}


def _record(history: list[dict[str, Any]]) -> tuple[str, ...]:
    return recall.Recall.of(history).record


_CREATED = ToolCallRecord(
    tool="create_task",
    arguments={"title": "Renew the car registration", "project_id": 2},
    result='{"id": 12, "title": "Renew the car registration"}',
)


def test_no_history_renders_nothing() -> None:
    assert recall.Recall.of([]).render(recall.PLANNER_REPLY_LABEL) == ""
    assert recall.section(recall.Recall.of([]), recall.PLANNER_REPLY_LABEL) is None


def test_the_last_reply_is_words_beside_the_record_of_what_ran() -> None:
    """The poisoned-thread shape: the reply says one thing, the record what
    the tools did. Both reach the planner, each under its own label."""
    history = [
        _user("Add renew the car registration to Home"),
        _assistant("Added it.", _CREATED),
        _user("Is the dentist one done?"),
        _assistant("Yep, the dentist task is done."),
    ]
    text = recall.Recall.of(history).render(recall.PLANNER_REPLY_LABEL)

    assert text == (
        "The app's record of earlier runs (what the tools did, oldest first):\n"
        '- create_task {"project_id":2,"title":"Renew the car registration"} -> '
        'id 12 "Renew the car registration" (asked: "Add renew the car registration to Home")\n'
        "\n"
        "What the user asked earlier, in their own words, oldest first:\n"
        '- "Add renew the car registration to Home"\n'
        "\n"
        "The last exchange:\n"
        'The user said: "Is the dentist one done?"\n'
        'The assistant said then (its words, not a record): "Yep, the dentist task is done."'
    )


def test_the_narrator_reads_the_last_reply_as_its_own() -> None:
    history = [_user("hi"), _assistant("yo")]
    text = recall.Recall.of(history).render(recall.NARRATOR_REPLY_LABEL)
    assert 'What you said then (your words, not a record): "yo"' in text


def test_a_run_with_no_reply_is_remembered_by_what_it_did() -> None:
    history = [_user("Make it"), _assistant(None, _CREATED)]
    text = recall.Recall.of(history).render(recall.PLANNER_REPLY_LABEL)
    assert "create_task" in text
    assert "The assistant said then (its words, not a record): nothing." in text


def test_reads_are_left_out_of_the_record_found_or_not() -> None:
    history = [
        _user("What's task 9?"),
        _assistant(
            "No such task.",
            ToolCallRecord(tool="list_tasks", arguments={}, result="[]"),
            ToolCallRecord(tool="get_task", arguments={"task_id": 9}, error="Task 9 not found"),
        ),
    ]
    assert _record(history) == ()


def test_a_refused_write_is_in_the_record() -> None:
    refused = ToolCallRecord(
        tool="complete_task", arguments={"task_id": 7}, error="Task 7 is blocked"
    )
    history = [_user("Finish 7"), _assistant("It's blocked.", refused)]
    (line,) = _record(history)
    assert line.startswith('refused: complete_task {"task_id":7} -> Task 7 is blocked')


def test_calls_that_never_ran_are_left_out() -> None:
    skipped = ToolCallRecord(
        tool="create_task", arguments={"title": "x"}, error="not run: the user stopped"
    )
    assert _record([_user("x"), _assistant(None, skipped)]) == ()


def test_parts_are_capped_and_say_what_they_dropped() -> None:
    history: list[dict[str, Any]] = []
    for index in range(60):
        history += [_user(f"ask number {index} " + "x" * 100), _assistant("ok", _CREATED)]
    earlier = recall.Recall.of(history)

    assert earlier.requests[0].endswith("earlier requests not listed)")
    assert earlier.record[0].endswith("earlier lines not listed)")
    assert sum(len(r) for r in earlier.requests[1:]) <= recall.REQUESTS_MAX_CHARS
    assert sum(len(r) for r in earlier.record[1:]) <= recall.RECORD_MAX_CHARS
    # The newest survive; the last exchange is shown whole, not as a request.
    assert "ask number 58" in earlier.requests[-1]
    assert earlier.last is not None and earlier.last[0].startswith("ask number 59")


def test_a_wall_of_text_in_the_last_exchange_is_cut_on_a_word() -> None:
    history = [_user("word " * 1000), _assistant("fine")]
    text = recall.Recall.of(history).render(recall.PLANNER_REPLY_LABEL)
    said = text.split('The user said: "', 1)[1].split('"', 1)[0]
    assert said.endswith("word…") and len(said) <= recall.LAST_EXCHANGE_CHARS + 1
