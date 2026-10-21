"""The eval gate's verdict, unit-tested off the GPU (#403).

``test_agent_evals.py`` needs the model on the GPU, so its *verdict* lives in
``tests/evalstats.py`` and this file is its spec, run on every commit. Ported
from chess's ``backend/tests/test_evalstats.py``
(ryanskarnulis/chess@0c088f18191cc2002f0df417191040f0ca88a284) for the parts
PCC copied, plus drift guards tying evalstats' literals to the app.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.ai import loop as loop_module
from app.ai.providers.llamacpp import LlamaCppProvider, ProviderError
from tests.evalstats import (
    DEFAULT_FLOOR,
    DETERMINISTIC_FAILURES,
    INFRA_STOPS,
    Z_ONE_SIDED_95,
    Decision,
    Outcome,
    RateResult,
    Stability,
    block_stability,
    classify,
    decide,
    failure_signature,
    format_paired,
    mcnemar_exact,
    paired_counts,
    provider_failure_kind,
    sample_seed,
    scenario_record,
    wilson_interval,
)

# --- wilson_interval ----------------------------------------------------------
#
# One-sided by construction: the red test uses one tail (is the *upper* end
# below the floor?), so the z that makes it a 95% test is 1.645.

# Upper bounds at n=5 for 0..5 passes: the reason 3/5 escalates instead of red.
_N5_UPPERS = [0.351, 0.565, 0.728, 0.857, 0.954, 1.000]


@pytest.mark.parametrize(("passed", "upper"), list(enumerate(_N5_UPPERS)))
def test_the_n5_upper_bound_table(passed: int, upper: float) -> None:
    assert wilson_interval(passed, 5)[1] == pytest.approx(upper, abs=0.001)


def test_the_default_z_and_floor_are_pinned_by_value() -> None:
    # The decision tables below (and docs/agent-design.md) are this z's and
    # this floor's; changing either is a gate change, not a refactor.
    assert Z_ONE_SIDED_95 == 1.645
    assert DEFAULT_FLOOR == 0.8


@pytest.mark.parametrize(
    ("passed", "upper"),
    [(0, 0.434), (1, 0.624), (2, 0.769), (3, 0.882), (4, 0.964), (5, 1.000)],
)
def test_z_is_a_parameter_and_a_two_tailed_bound_is_wider(passed: int, upper: float) -> None:
    assert wilson_interval(passed, 5, 1.96)[1] == pytest.approx(upper, abs=0.001)


def test_twenty_samples_narrow_the_interval() -> None:
    assert wilson_interval(17, 20) == pytest.approx((0.678, 0.938), abs=0.001)
    assert wilson_interval(12, 20) == pytest.approx((0.419, 0.758), abs=0.001)


def test_no_samples_is_no_evidence() -> None:
    assert wilson_interval(0, 0) == (0.0, 1.0)


def test_the_interval_never_leaves_the_unit_range() -> None:
    low, high = wilson_interval(5, 5)
    assert high == 1.0 and 0.0 < low < 1.0
    low, high = wilson_interval(0, 5)
    assert low == 0.0 and 0.0 < high < 1.0


# --- decide -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("passed", "expected"),
    [
        (0, Decision.BELOW_FLOOR),
        (1, Decision.BELOW_FLOOR),
        (2, Decision.BELOW_FLOOR),
        (3, Decision.UNDECIDED),
        (4, Decision.ABOVE_FLOOR),
        (5, Decision.ABOVE_FLOOR),
    ],
)
def test_the_n5_decision_table(passed: int, expected: Decision) -> None:
    """A healthy scenario (4/5, 5/5) costs exactly 5 samples and a hopeless one
    (0–2) stops at 5; only the ambiguous 3/5 pays for another block."""
    assert decide(passed, 5, 0.8) == expected


def test_twenty_samples_resolve_the_band() -> None:
    assert decide(17, 20, 0.8) == Decision.ABOVE_FLOOR
    assert decide(12, 20, 0.8) == Decision.BELOW_FLOOR


def test_the_escalation_path_from_three_of_five() -> None:
    # 3/5 → another block: 7/10 still straddles, 8/10 is green, 5/10 is red.
    assert decide(7, 10, 0.8) == Decision.UNDECIDED
    assert decide(8, 10, 0.8) == Decision.ABOVE_FLOOR
    assert decide(5, 10, 0.8) == Decision.BELOW_FLOOR


def test_a_single_sample_decides_on_its_own() -> None:
    # PCC_AGENT_EVALS_RUNS=1 keeps the pre-#403 one-run behavior: a pass is
    # green, a miss is red (0/1's upper bound is 0.73).
    assert decide(1, 1, 0.8) == Decision.ABOVE_FLOOR
    assert decide(0, 1, 0.8) == Decision.BELOW_FLOOR


def test_the_point_estimate_alone_decides_a_pass() -> None:
    assert wilson_interval(5, 5)[0] < 0.8
    assert decide(5, 5, 0.8) == Decision.ABOVE_FLOOR


def test_the_floor_is_a_parameter() -> None:
    assert decide(3, 5, 0.6) == Decision.ABOVE_FLOOR
    assert decide(2, 5, 0.5) == Decision.UNDECIDED
    assert decide(0, 5, 0.5) == Decision.BELOW_FLOOR


def test_z_widens_the_undecided_band() -> None:
    assert decide(3, 5, 0.8, z=1.96) == Decision.UNDECIDED
    assert decide(2, 5, 0.8, z=1.96) == Decision.BELOW_FLOOR


# --- classify -----------------------------------------------------------------


def test_a_clean_sample_that_passed_its_check_is_a_pass() -> None:
    assert classify(status_code=200, stop_reason="completed", error=None) == Outcome.PASS


def test_a_failed_assertion_on_a_healthy_run_is_a_real_failure() -> None:
    outcome = classify(
        status_code=200, stop_reason="completed", error=AssertionError("no create_task")
    )
    assert outcome == Outcome.FAIL


def test_a_limit_stop_is_behavior_not_infrastructure() -> None:
    # max_iterations, no_progress, … are the model's doing: scored.
    for stop in ("max_iterations", "correction_limit", "no_progress", "tool_call_limit"):
        assert (
            classify(status_code=200, stop_reason=stop, error=AssertionError(stop))
            == Outcome.FAIL
        )


@pytest.mark.parametrize("stop", sorted(INFRA_STOPS))
def test_a_provider_death_or_timeout_is_infrastructure(stop: str) -> None:
    statuses = {str(k): v for k, v in loop_module._FAILURE_STATUS.items()}  # noqa: SLF001
    status = statuses[stop]
    assert classify(status_code=status, stop_reason=stop, error=None) == Outcome.INFRA


def test_a_rate_limited_route_is_infrastructure() -> None:
    """The agent route allows 10 messages a minute per client: a 429 is the
    harness outrunning the limiter, never a miss."""
    assert classify(status_code=429, stop_reason=None, error=None) == Outcome.INFRA


def test_infrastructure_beats_a_failed_assertion() -> None:
    outcome = classify(
        status_code=502, stop_reason="provider_error", error=AssertionError("no reply")
    )
    assert outcome == Outcome.INFRA


def test_an_internal_error_is_scored_not_retried() -> None:
    # A tool that raised (500, internal_error) is the app's doing, and a retry
    # would only hide it.
    assert classify(status_code=500, stop_reason="internal_error", error=None) == Outcome.FAIL


def test_any_other_exception_is_a_harness_bug() -> None:
    assert classify(status_code=200, stop_reason="completed", error=KeyError("x")) == (
        Outcome.HARNESS
    )


def test_a_harness_bug_outranks_a_dead_provider() -> None:
    outcome = classify(status_code=502, stop_reason="provider_error", error=KeyError("x"))
    assert outcome == Outcome.HARNESS


@pytest.mark.parametrize("kind", sorted(DETERMINISTIC_FAILURES))
def test_a_rejected_request_is_not_retried(kind: str) -> None:
    outcome = classify(
        status_code=502, stop_reason="provider_error", error=None, provider_failure=kind
    )
    assert outcome == Outcome.PROVIDER_REJECTED


@pytest.mark.parametrize("kind", ["transport", "http_503", "http_429", None])
def test_a_dead_or_busy_server_is_still_retried(kind: str | None) -> None:
    outcome = classify(
        status_code=502, stop_reason="provider_error", error=None, provider_failure=kind
    )
    assert outcome == Outcome.INFRA


def test_the_infra_stops_are_the_loops_own() -> None:
    # Literals in evalstats, so the duplication is paid for here.
    assert INFRA_STOPS <= set(loop_module._FAILURE_STATUS)  # noqa: SLF001


# --- provider_failure_kind, against the real provider's messages --------------


def _death(handler: httpx.MockTransport) -> str:
    provider = LlamaCppProvider(
        "http://llm.test/v1", "gemma-4-12b", client=httpx.Client(transport=handler)
    )
    with pytest.raises(ProviderError) as caught:
        provider.chat([{"role": "user", "content": "hi"}])
    return str(caught.value)


def test_a_4xx_from_the_server_is_a_rejection() -> None:
    message = _death(httpx.MockTransport(lambda _r: httpx.Response(400, text="ctx overrun")))
    assert provider_failure_kind(message) == "rejected"
    # The route's 502 detail wraps it; the kind survives the wrapping.
    assert provider_failure_kind(f"agent run failed: {message}") == "rejected"


def test_a_5xx_or_a_429_from_the_server_is_retried() -> None:
    for status in (503, 429):
        message = _death(httpx.MockTransport(lambda _r, s=status: httpx.Response(s, text="")))
        assert provider_failure_kind(message) == f"http_{status}"


def test_a_dead_socket_is_transport() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    assert provider_failure_kind(_death(httpx.MockTransport(refuse))) == "transport"


def test_an_unparseable_body_is_malformed() -> None:
    message = _death(httpx.MockTransport(lambda _r: httpx.Response(200, json={"nope": 1})))
    assert provider_failure_kind(message) == "malformed_response"


# --- failure_signature --------------------------------------------------------


def test_failures_differing_only_in_numbers_share_a_signature() -> None:
    assert failure_signature("over-searched: 9 iterations (ceiling 7)") == failure_signature(
        "over-searched: 11 iterations (ceiling 7)"
    )


def test_genuinely_different_failures_stay_apart() -> None:
    assert failure_signature("expected a successful create_task") != failure_signature(
        "wrong count: 'four'"
    )


def test_a_signature_is_readable() -> None:
    assert failure_signature("wrong count:  '13'\n") == "wrong count: '<n>'"


# --- block_stability ----------------------------------------------------------


def test_a_five_five_then_zero_five_pair_is_unstable() -> None:
    assert block_stability([(5, 5), (0, 5)]) == Stability.UNSTABLE


def test_ordinary_sampling_spread_is_stable() -> None:
    assert block_stability([(5, 5), (3, 5)]) == Stability.STABLE


def test_one_block_cannot_be_unstable() -> None:
    assert block_stability([(3, 5)]) == Stability.STABLE
    assert block_stability([]) == Stability.STABLE


def test_empty_blocks_do_not_count_as_zero_rates() -> None:
    assert block_stability([(5, 5), (0, 0)]) == Stability.STABLE


# --- RateResult ---------------------------------------------------------------


def test_rate_result_derives_its_own_verdict() -> None:
    result = RateResult.from_blocks(
        blocks=[(5, 5), (3, 5)], failures=["run 7: wrong count: '4'"], floor=0.8
    )
    assert (result.passed, result.runs, result.rate) == (8, 10, 0.8)
    assert result.blocks == ((5, 5), (3, 5))
    assert result.decision == Decision.ABOVE_FLOOR
    assert result.stability == Stability.STABLE
    assert result.interval == wilson_interval(8, 10)


def test_a_scenario_below_the_floor_reads_as_a_regression() -> None:
    result = RateResult.from_blocks(
        blocks=[(1, 5)], failures=["no create_task"] * 4, floor=0.8
    )
    assert result.decision == Decision.BELOW_FLOOR
    assert result.rate == 0.2


def test_failure_modes_are_a_histogram() -> None:
    result = RateResult.from_blocks(
        blocks=[(1, 5)],
        failures=["run 1: wrong count: '4'", "run 2: wrong count: '2'", "run 3: x", "run 4: x"],
        floor=0.8,
    )
    assert sorted(result.failure_modes.values()) == [2, 2]


def test_a_uniform_total_failure_is_a_deterministic_suspect() -> None:
    result = RateResult.from_blocks(blocks=[(0, 5)], failures=["no create_task"] * 5, floor=0.8)
    assert result.deterministic_suspect is True


def test_mixed_or_partial_failures_are_not_deterministic_suspects() -> None:
    mixed = RateResult.from_blocks(blocks=[(0, 5)], failures=["a", "b"], floor=0.8)
    partial = RateResult.from_blocks(blocks=[(2, 5)], failures=["a"] * 3, floor=0.8)
    assert not mixed.deterministic_suspect and not partial.deterministic_suspect


def test_the_summary_line_carries_the_verdict_and_its_cost() -> None:
    result = RateResult.from_blocks(blocks=[(4, 5)], failures=["x"], floor=0.8, infra=1)
    summary = result.summary()
    assert "4/5" in summary
    assert "0.44" in summary and "0.95" in summary
    assert "infra=1" in summary
    assert str(Decision.ABOVE_FLOOR) in summary


def test_zero_runs_has_a_rate_of_zero_rather_than_an_exception() -> None:
    result = RateResult.from_blocks(blocks=[], failures=[], floor=0.8, infra=5)
    assert (result.rate, result.runs, result.interval) == (0.0, 0, (0.0, 1.0))


def test_a_scenario_record_is_json_and_carries_the_whole_verdict() -> None:
    result = RateResult.from_blocks(
        blocks=[(5, 5), (2, 5)], failures=["run 8: no create_task"], floor=0.8, infra=2
    )
    record = json.loads(
        json.dumps(scenario_record("delegate_actor", result, 0.8, samples=[{"seed": 1}]))
    )
    assert record["scenario"] == "delegate_actor"
    assert (record["passed"], record["runs"], record["floor"]) == (7, 10, 0.8)
    assert record["blocks"] == [[5, 5], [2, 5]]
    assert record["decision"] == "UNDECIDED"
    assert record["stability"] == "UNSTABLE"
    assert record["infra"] == 2
    assert record["failure_modes"] == {"run <n>: no create_task": 1}
    assert record["samples"] == [{"seed": 1}]


# --- paired arms --------------------------------------------------------------


def test_paired_counts_split_aligned_samples_into_the_four_cells() -> None:
    a = [True, True, False, False, True]
    b = [True, False, True, False, True]
    assert paired_counts(a, b) == (2, 1, 1, 1)


def test_paired_counts_refuse_unaligned_arms() -> None:
    with pytest.raises(ValueError, match="unaligned"):
        paired_counts([True], [True, False])


@pytest.mark.parametrize(
    ("a_only", "b_only", "p"),
    [(0, 0, 1.0), (0, 6, 0.03125), (6, 0, 0.03125), (1, 9, 0.021484375), (3, 3, 1.0)],
)
def test_mcnemar_exact_is_the_two_sided_binomial_on_discordant_pairs(
    a_only: int, b_only: int, p: float
) -> None:
    assert mcnemar_exact(a_only, b_only) == pytest.approx(p)


def test_concordant_pairs_do_not_move_the_verdict() -> None:
    assert format_paired(1, 1, 20) == "pairs 20 · A-only 1 · B-only 1 · p=1"


def test_sample_seed_is_keyed_on_the_scored_position() -> None:
    assert sample_seed(None, 3) is None
    assert [sample_seed(5000, j) for j in range(3)] == [5000, 5001, 5002]
