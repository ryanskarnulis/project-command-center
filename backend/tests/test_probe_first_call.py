"""The first-call probe's pure parts, off the GPU (#410).

``scripts/probe_first_call.py`` makes the loop's first model call N times per
arm. Everything here runs without a server: the classifier, the arm grammar,
the override mechanics, the interleaving schedule, the table, and — the
no-drift guarantee — that the control arm's request is the loop's own.
"""

from __future__ import annotations

import importlib.util
import sys
import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from app.ai import loop as agent_loop
from app.ai import state_block
from app.ai.model_profile import load_profile
from app.ai.providers.llamacpp import (
    ChatResult,
    ProviderRequestError,
    ToolCall,
    ToolCallArgumentsError,
    ToolSpec,
)
from app.tools import registry

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "probe_first_call.py"


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("probe_first_call", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


probe = _load()
MODEL = "gemma-4-12b"


def _result(*names: str, finish: str = "stop") -> ChatResult:
    calls = [ToolCall(id=f"c{i}", name=n, arguments={}) for i, n in enumerate(names)]
    return ChatResult(content=None, tool_calls=calls, finish_reason=finish, usage=None)


# --- classification -----------------------------------------------------------


def test_classify_names_each_call_or_no_tool() -> None:
    assert probe.classify(_result()) == ("no_tool",)
    assert probe.classify(_result("list_projects")) == ("tool:list_projects",)
    assert probe.label(probe.classify(_result("list_projects", "create_task"))) == (
        "tool:list_projects + tool:create_task"
    )


def test_a_reply_that_ran_out_of_tokens_is_still_no_tool() -> None:
    assert probe.classify(_result(finish="length")) == ("no_tool",)


def test_classify_error_separates_a_chosen_tool_from_a_dead_server() -> None:
    assert probe.classify_error(ToolCallArgumentsError("create_task", "not json")) == (
        "bad_args:create_task"
    )
    dead = probe.classify_error(ProviderRequestError("boom"))
    assert dead == "error:ProviderRequestError" and probe.is_infra(dead)
    assert not probe.is_infra("bad_args:create_task")


def test_passes_matches_fnmatch_patterns_on_the_joined_label() -> None:
    assert probe.passes(["tool:*"], "tool:a + tool:b")
    assert not probe.passes(["tool:*"], "no_tool")
    assert probe.passes(["no_tool"], "no_tool")
    assert probe.passes(["tool:create_task*"], "tool:create_task + tool:x")
    assert not probe.passes([], "no_tool")


# --- arms ---------------------------------------------------------------------


def test_parse_arm_reads_name_and_settings() -> None:
    assert probe.parse_arm("control") == probe.Arm("control")
    arm = probe.parse_arm(
        "cold:temperature=0.2,model=other,profile=p,prompt=/x/p.txt,tools=/x/t.json"
    )
    assert arm.temperature == 0.2 and arm.model == "other" and arm.profile == "p"
    assert arm.prompt == Path("/x/p.txt") and arm.tools == Path("/x/t.json")


@pytest.mark.parametrize(
    "spec", [":x=1", "a:bogus=1", "a:temperature", "a:temperature=", "a:model=x,model=y"]
)
def test_parse_arm_rejects_what_it_does_not_understand(spec: str) -> None:
    with pytest.raises(ValueError):
        probe.parse_arm(spec)


def test_arm_order_rotates_so_no_arm_is_always_first() -> None:
    arms = [probe.Arm("a"), probe.Arm("b"), probe.Arm("c")]
    firsts = [probe.arm_order(arms, i)[0].name for i in range(6)]
    assert firsts == ["a", "b", "c", "a", "b", "c"]
    assert sorted(a.name for a in probe.arm_order(arms, 4)) == ["a", "b", "c"]


def test_seed_is_paired_across_arms_and_optional() -> None:
    assert probe.seed_for(100, 3) == 103
    assert probe.seed_for(None, 3) is None


def test_arm_temperature_is_the_planner_s_and_reaches_the_wire() -> None:
    base = load_profile(MODEL)
    arm = probe.arm_profile(probe.Arm("t", temperature=0.1), MODEL)
    assert arm.turn_temperature == 0.1
    assert arm.sampling == base.sampling
    assert probe.arm_profile(probe.Arm("c"), MODEL) == base
    item = probe.Item("i", "add a task to buy milk")
    payload = probe.dry_run_payload(probe.Arm("t", temperature=0.1), item, MODEL, seed=None)
    assert payload["temperature"] == 0.1 and payload["top_p"] == base.sampling.top_p


# --- overrides ----------------------------------------------------------------


def _specs() -> list[ToolSpec]:
    return registry.tool_specs()


def test_tool_override_replaces_a_description_and_leaves_the_registry_alone() -> None:
    specs = _specs()
    name = specs[0].name
    original = specs[0].description
    out = probe.apply_tool_overrides(specs, {name: "NEW TEXT"})
    assert out[0].description == "NEW TEXT"
    assert registry.tool_specs()[0].description == original
    assert [s.name for s in out] == [s.name for s in specs]


def test_param_override_edits_only_that_parameter() -> None:
    specs = _specs()
    spec = next(s for s in specs if s.parameters.get("properties"))
    param = next(iter(spec.parameters["properties"]))
    before = json.dumps(spec.parameters, sort_keys=True)
    out = probe.apply_tool_overrides(specs, {f"{spec.name}.{param}": "PARAM TEXT"})
    edited = next(s for s in out if s.name == spec.name)
    assert edited.parameters["properties"][param]["description"] == "PARAM TEXT"
    assert json.dumps(spec.parameters, sort_keys=True) == before


@pytest.mark.parametrize("key", ["no_such_tool", "no_such_tool.x"])
def test_an_override_that_names_nothing_is_an_error(key: str) -> None:
    with pytest.raises(ValueError):
        probe.apply_tool_overrides(_specs(), {key: "x"})


def test_override_of_a_missing_parameter_is_an_error() -> None:
    with pytest.raises(ValueError):
        probe.apply_tool_overrides(_specs(), {f"{_specs()[0].name}.no_such_param": "x"})


def test_prompt_override_keeps_the_date_line() -> None:
    messages = [{"role": "system", "content": "old"}, {"role": "user", "content": "hi"}]
    out = probe.override_system_prompt(messages, "  new rules  ", date(2026, 1, 2))
    assert out[0]["content"] == "new rules"
    assert out[1] == messages[1] and messages[0]["content"] == "old"
    placed = probe.override_system_prompt(messages, "on {today}.", date(2026, 1, 2))
    assert placed[0]["content"] == "on 2026-01-02."


# --- the shipped call, not a copy ---------------------------------------------


def test_control_arm_request_is_what_the_loop_sends() -> None:
    item = probe.Item("i", "add a task to buy milk")
    payload = probe.dry_run_payload(probe.Arm("control"), item, MODEL, seed=7)
    profile = load_profile(MODEL)
    assert payload["messages"] == [
        {"role": "system", "content": agent_loop.PLANNER_PROMPT},
        {
            "role": "user",
            "content": state_block.with_state(
                state_block.build(date.today()), "add a task to buy milk"
            ),
        },
    ]
    assert payload["tools"] == [spec.to_wire() for spec in registry.tool_specs()]
    assert payload["max_tokens"] == profile.turn_max_tokens
    assert payload["model"] == MODEL and payload["seed"] == 7
    # The first call is the planner's, at its own temperature (#418).
    planner = {**profile.sampling.wire(), "temperature": profile.turn_temperature}
    for key, value in planner.items():
        assert payload[key] == value


def test_history_goes_through_the_loops_own_recall() -> None:
    history = [{"role": "user", "content": "earlier"}, {"role": "assistant", "content": "ok"}]
    item = probe.Item("i", "and now", history=tuple(history))
    payload = probe.dry_run_payload(probe.Arm("control"), item, MODEL, seed=None)
    [user] = payload["messages"][1:]
    assert 'The user said: "earlier"' in user["content"]
    assert user["content"].endswith("\n\nand now")
    assert "seed" not in payload


def test_arms_change_only_the_knob_they_name(tmp_path: Path) -> None:
    prompt = tmp_path / "p.txt"
    prompt.write_text("PROMPT B")
    tools = tmp_path / "t.json"
    name = registry.tool_specs()[0].name
    tools.write_text(json.dumps({name: "TOOL B"}))
    item = probe.Item("i", "hello")
    control = probe.dry_run_payload(probe.Arm("c"), item, MODEL, seed=1)
    other = probe.dry_run_payload(
        probe.Arm("o", prompt=prompt, tools=tools, temperature=0.05), item, MODEL, seed=1
    )
    assert other["messages"][0]["content"].startswith("PROMPT B")
    assert other["tools"][0]["function"]["description"] == "TOOL B"
    assert other["tools"][1:] == control["tools"][1:]
    assert other["temperature"] == 0.05
    assert other["messages"][1:] == control["messages"][1:]
    assert other["seed"] == control["seed"] == 1


# --- the table ----------------------------------------------------------------


def _sample(arm: str, index: int, klass: str, item: str = "i") -> Any:
    return probe.Sample(arm, item, index, 1000 + index, klass, "stop", 100 + index, 5)


def test_tables_count_classes_pass_and_pair_against_the_first_arm() -> None:
    item = probe.Item("i", "hello", expect=("no_tool",))
    arms = [probe.Arm("control"), probe.Arm("new")]
    samples = [
        _sample("control", 0, "no_tool"),
        _sample("new", 0, "no_tool"),
        _sample("control", 1, "tool:a"),
        _sample("new", 1, "no_tool"),
        _sample("control", 2, "tool:a"),
        _sample("new", 2, "error:ProviderRequestError"),  # the server's, not scored
        _sample("control", 3, "tool:a + tool:b"),
        _sample("new", 3, "no_tool"),
    ]
    tally = probe.tally(samples, "new")
    assert tally.n == 3 and tally.errors == 1
    text = probe.format_tables(samples, arms, [item])
    assert "control  n=4 pass 1/4" in text
    assert "new      n=3 pass 3/3 errors 1" in text
    # Shared scored indexes 0, 1, 3: control passes one, new passes all three.
    assert "vs control: pairs 3 · A-only 0 · B-only 2 · p=0.5" in text


def test_unscored_items_print_counts_only() -> None:
    item = probe.Item("i", "hello")
    text = probe.format_tables([_sample("control", 0, "no_tool")], [probe.Arm("control")], [item])
    assert "pass " not in text.split("\n", 2)[2] and "no_tool 1" in text


def test_load_items_is_strict(tmp_path: Path) -> None:
    good = tmp_path / "good.json"
    good.write_text(json.dumps([{"id": "a", "ask": "x", "expect": ["no_tool"]}]))
    assert probe.load_items(good) == [probe.Item("a", "x", expect=("no_tool",))]
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps([{"id": "a", "ask": "x", "typo": 1}]))
    with pytest.raises(SystemExit):
        probe.load_items(bad)
    dup = tmp_path / "dup.json"
    dup.write_text(json.dumps([{"id": "a", "ask": "x"}, {"id": "a", "ask": "y"}]))
    with pytest.raises(SystemExit):
        probe.load_items(dup)
