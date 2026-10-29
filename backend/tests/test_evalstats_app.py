"""PCC's drift guards for the vendored eval gate.

``tests/evalstats.py`` and its spec ``test_evalstats.py`` are vendored from
agent-standard and import nothing of the app, so the literals they read — the
loop's failure stops and statuses, and the provider's error text — are pinned
against PCC's own loop and provider here.
"""

from __future__ import annotations

import httpx
import pytest

from app.ai import loop as loop_module
from app.ai.providers.llamacpp import LlamaCppProvider, ProviderError
from tests.evalstats import INFRA_STATUSES, INFRA_STOPS, provider_failure_kind


def test_the_infra_stops_are_the_loops_own() -> None:
    assert INFRA_STOPS <= set(loop_module._FAILURE_STATUS)  # noqa: SLF001


def test_the_infra_statuses_are_what_the_loop_answers_infra_stops_with() -> None:
    by_stop = {str(stop): status for stop, status in loop_module._FAILURE_STATUS.items()}  # noqa: SLF001
    assert {by_stop[stop] for stop in INFRA_STOPS} == INFRA_STATUSES


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
