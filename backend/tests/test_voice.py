"""Voice endpoints: STT/TTS proxied through the shared speech service.

The browser never talks to the speech servers directly — it posts audio to
the app, which forwards it and hands back plain text destined for the same
agent pipeline as typed input. The speech layer speaks the OpenAI audio wire
format over plain httpx per the fleet contract (``../agent-standard/voice.md``);
tests inject an ``httpx.MockTransport``, so no live speech server (or audio
model) is ever required.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Generator

import httpx
import pytest
from fastapi.testclient import TestClient

from app.ai.speech import (
    STT_PROMPT,
    SpeechClient,
    SpeechRequestError,
    SpeechResponseError,
    speech_client_from_settings,
)
from app.api.routes_voice import MAX_AUDIO_BYTES, MAX_SPEAK_TEXT_LENGTH
from app.api import routes_voice
from app.config import get_settings
from app.main import app


class FakeSpeechServer:
    """An OpenAI-audio-shaped server behind ``httpx.MockTransport``.

    Records every request so tests can assert on the wire: multipart fields
    for /audio/transcriptions, the JSON body for /audio/speech.
    """

    def __init__(
        self,
        text: str = "add a task to buy milk tomorrow",
        audio: bytes = b"mp3-bytes",
        status: int = 200,
        body: dict[str, str] | None = None,
    ) -> None:
        self.text = text
        self.audio = audio
        self.status = status
        self.body = body  # overrides the transcription JSON body when set
        self.requests: list[httpx.Request] = []

    def _handler(self, request: httpx.Request) -> httpx.Response:
        request.read()
        self.requests.append(request)
        if self.status != 200:
            return httpx.Response(self.status, text="upstream sad")
        if request.url.path.endswith("/audio/transcriptions"):
            body = self.body if self.body is not None else {"text": self.text}
            return httpx.Response(200, json=body)
        if request.url.path.endswith("/audio/speech"):
            return httpx.Response(
                200, content=self.audio, headers={"content-type": "audio/mpeg"}
            )
        return httpx.Response(404)

    def client(self, base_url: str = "http://speech:8400/v1") -> httpx.Client:
        return httpx.Client(
            base_url=base_url, transport=httpx.MockTransport(self._handler)
        )


# --- SpeechClient unit --------------------------------------------------------


def test_transcribe_forwards_audio_and_returns_text() -> None:
    server = FakeSpeechServer(text="move it to focus")
    speech = SpeechClient(client=server.client(), stt_model="whisper-test")
    text = speech.transcribe(b"opus-bytes", filename="clip.webm")
    assert text == "move it to focus"
    (request,) = server.requests
    assert request.url.path == "/v1/audio/transcriptions"
    body = request.read()
    assert b'filename="clip.webm"' in body
    assert b"opus-bytes" in body
    assert b"whisper-test" in body


def test_transcribe_biases_whisper_with_the_pcc_vocabulary_prompt() -> None:
    server = FakeSpeechServer()
    speech = SpeechClient(client=server.client())
    speech.transcribe(b"opus-bytes")
    (request,) = server.requests
    assert STT_PROMPT.encode() in request.read()


def test_stt_prompt_covers_the_task_vocabulary() -> None:
    # The prompt biases recognition toward PCC's domain phrasing; whisper also
    # mimics its formatting, so dates appear as they should transcribe.
    for term in ("task", "project", "due", "focus", "overdue", "priority"):
        assert term in STT_PROMPT


def test_speak_forwards_text_and_returns_audio_bytes() -> None:
    server = FakeSpeechServer(audio=b"kokoro-mp3")
    speech = SpeechClient(
        client=server.client(), tts_model="tts-test", tts_voice="af_test"
    )
    assert speech.speak("Task created.") == b"kokoro-mp3"
    (request,) = server.requests
    assert request.url.path == "/v1/audio/speech"
    assert json.loads(request.read()) == {
        "model": "tts-test",
        "voice": "af_test",
        "input": "Task created.",
        "response_format": "mp3",
    }


def test_speak_uses_the_dedicated_tts_client_when_given() -> None:
    stt_server = FakeSpeechServer()
    tts_server = FakeSpeechServer(audio=b"house-voice-mp3")
    speech = SpeechClient(client=stt_server.client(), tts_client=tts_server.client())
    assert speech.speak("Done.") == b"house-voice-mp3"
    assert stt_server.requests == []
    assert speech.transcribe(b"opus-bytes") == "add a task to buy milk tomorrow"
    assert len(tts_server.requests) == 1  # STT never touches the TTS client


# --- typed errors -------------------------------------------------------------


def test_transcribe_upstream_http_error_raises_request_error() -> None:
    speech = SpeechClient(client=FakeSpeechServer(status=500).client())
    with pytest.raises(SpeechRequestError):
        speech.transcribe(b"opus-bytes")


def test_transcribe_unreachable_server_raises_request_error() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = httpx.Client(
        base_url="http://speech:8400/v1", transport=httpx.MockTransport(refuse)
    )
    with pytest.raises(SpeechRequestError):
        SpeechClient(client=client).transcribe(b"opus-bytes")


def test_transcribe_malformed_body_raises_response_error() -> None:
    speech = SpeechClient(client=FakeSpeechServer(body={"nope": "x"}).client())
    with pytest.raises(SpeechResponseError):
        speech.transcribe(b"opus-bytes")


def test_speak_upstream_http_error_raises_request_error() -> None:
    speech = SpeechClient(client=FakeSpeechServer(status=503).client())
    with pytest.raises(SpeechRequestError):
        speech.speak("Done.")


# --- settings factory ---------------------------------------------------------


def test_factory_returns_none_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "speech_base_url", None)
    assert speech_client_from_settings() is None


def test_factory_returns_none_for_empty_speech_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Deployments opt out with an empty value — a compose .env cannot "unset".
    monkeypatch.setattr(get_settings(), "speech_base_url", "")
    assert speech_client_from_settings() is None


def test_factory_builds_split_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "speech_base_url", "http://speech:8400/v1")
    monkeypatch.setattr(get_settings(), "tts_base_url", "http://kokoro:8410/v1")
    speech = speech_client_from_settings()
    assert speech is not None
    assert "speech:8400" in str(speech.client.base_url)
    assert speech.tts_client is not None
    assert "kokoro:8410" in str(speech.tts_client.base_url)
    speech.close()


def test_factory_single_backend_without_tts_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "speech_base_url", "http://speech:8400/v1")
    monkeypatch.setattr(get_settings(), "tts_base_url", None)
    speech = speech_client_from_settings()
    assert speech is not None
    assert speech.tts_client is None
    speech.close()


# --- API endpoints -------------------------------------------------------------


@pytest.fixture
def voice_client() -> Generator[TestClient, None, None]:
    """App client with a fake speech backend injected."""
    server = FakeSpeechServer()
    app.dependency_overrides[routes_voice.get_speech_client] = lambda: SpeechClient(
        client=server.client()
    )
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def test_transcribe_endpoint_returns_text(voice_client: TestClient) -> None:
    response = voice_client.post(
        "/api/voice/transcribe",
        files={"audio": ("clip.webm", b"opus-bytes", "audio/webm")},
    )
    assert response.status_code == 200
    assert response.json() == {"text": "add a task to buy milk tomorrow"}


def test_speak_endpoint_returns_audio(voice_client: TestClient) -> None:
    response = voice_client.post("/api/voice/speak", json={"text": "Task created."})
    assert response.status_code == 200
    assert response.content == b"mp3-bytes"
    assert response.headers["content-type"] == "audio/mpeg"


def test_speak_rejects_blank_text(voice_client: TestClient) -> None:
    assert (
        voice_client.post("/api/voice/speak", json={"text": "   "}).status_code == 422
    )


def test_transcribe_without_speech_service_is_503() -> None:
    app.dependency_overrides[routes_voice.get_speech_client] = lambda: None
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/voice/transcribe",
                files={"audio": ("clip.webm", b"opus-bytes", "audio/webm")},
            )
        assert response.status_code == 503
    finally:
        app.dependency_overrides.clear()


def test_speak_without_speech_service_is_503() -> None:
    app.dependency_overrides[routes_voice.get_speech_client] = lambda: None
    try:
        with TestClient(app) as client:
            response = client.post("/api/voice/speak", json={"text": "hi"})
        assert response.status_code == 503
    finally:
        app.dependency_overrides.clear()


def test_transcribe_upstream_failure_is_502() -> None:
    server = FakeSpeechServer(status=500)
    app.dependency_overrides[routes_voice.get_speech_client] = lambda: SpeechClient(
        client=server.client()
    )
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/voice/transcribe",
                files={"audio": ("clip.webm", b"opus-bytes", "audio/webm")},
            )
        assert response.status_code == 502
    finally:
        app.dependency_overrides.clear()


def test_speak_upstream_failure_is_502() -> None:
    server = FakeSpeechServer(status=503)
    app.dependency_overrides[routes_voice.get_speech_client] = lambda: SpeechClient(
        client=server.client()
    )
    try:
        with TestClient(app) as client:
            response = client.post("/api/voice/speak", json={"text": "hi"})
        assert response.status_code == 502
    finally:
        app.dependency_overrides.clear()


def test_voice_endpoints_are_rate_limited(
    voice_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "voice_requests_per_min", 1)
    ok = voice_client.post("/api/voice/speak", json={"text": "one"})
    assert ok.status_code == 200
    throttled = voice_client.post("/api/voice/speak", json={"text": "two"})
    assert throttled.status_code == 429
    assert "Retry-After" in throttled.headers


# --- concurrency --------------------------------------------------------------


class BlockingSpeechClient:
    """A ``SpeechClient`` stand-in whose transcribe blocks the calling thread.

    Stands in for a slow (or cold-starting) upstream whisper server without
    needing one: what matters is that the block happens off the event loop.
    """

    def __init__(self, delay: float) -> None:
        self.delay = delay

    def transcribe(self, data: bytes, filename: str = "audio.webm") -> str:
        time.sleep(self.delay)
        return "slow transcript"


async def test_transcribe_does_not_block_the_event_loop() -> None:
    """A slow STT round trip must not stall unrelated coroutines.

    A background coroutine ticks every 5 ms for the whole request; before the
    threadpool offload one of those ticks swallowed the entire 250 ms
    synchronous speech call, so the largest gap between ticks exceeded it.
    """
    blocking = BlockingSpeechClient(delay=0.25)
    app.dependency_overrides[routes_voice.get_speech_client] = lambda: blocking
    gaps: list[float] = []

    async def ticker() -> None:
        last = time.perf_counter()
        while True:
            await asyncio.sleep(0.005)
            now = time.perf_counter()
            gaps.append(now - last)
            last = now

    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            beat = asyncio.create_task(ticker())
            response = await client.post(
                "/api/voice/transcribe",
                files={"audio": ("clip.webm", b"opus-bytes", "audio/webm")},
            )
            beat.cancel()
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"text": "slow transcript"}
    assert gaps, "the ticker never ran"
    # Generous bound: no single tick may have waited out the 250 ms STT call.
    assert max(gaps) < 0.15, f"event loop was blocked for {max(gaps):.3f}s"


# --- payload limits (#245) -----------------------------------------------------


class RecordingSpeechClient:
    """A ``SpeechClient`` stand-in that records whether it was reached at all.

    Rejected input must never touch the speech service: the whole point of the
    limits is that oversized bytes are not forwarded upstream.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []

    def transcribe(self, data: bytes, filename: str = "audio.webm") -> str:
        self.calls.append("transcribe")
        return "transcript"

    def speak(self, text: str) -> bytes:
        self.calls.append("speak")
        return b"mp3-bytes"


@pytest.fixture
def recording_client() -> Generator[
    tuple[TestClient, RecordingSpeechClient], None, None
]:
    speech = RecordingSpeechClient()
    app.dependency_overrides[routes_voice.get_speech_client] = lambda: speech
    with TestClient(app) as client:
        yield client, speech
    app.dependency_overrides.clear()


_BOUNDARY = "pccvoiceboundary"
_MULTIPART_HEAD = (
    f"--{_BOUNDARY}\r\n"
    'Content-Disposition: form-data; name="audio"; filename="clip.wav"\r\n'
    "Content-Type: audio/wav\r\n\r\n"
).encode()
_MULTIPART_TAIL = f"\r\n--{_BOUNDARY}--\r\n".encode()
# What the multipart envelope costs on top of the audio itself. The ceiling
# bounds the whole request body, so the tests size the audio to hit it exactly.
_ENVELOPE = len(_MULTIPART_HEAD) + len(_MULTIPART_TAIL)
_MULTIPART_TYPE = f"multipart/form-data; boundary={_BOUNDARY}"
_CHUNK = 64 * 1024


def _upload_of_total_size(total: int) -> bytes:
    """A multipart body whose *whole* length is exactly ``total`` bytes."""
    body = _MULTIPART_HEAD + b"\0" * (total - _ENVELOPE) + _MULTIPART_TAIL
    assert len(body) == total
    return body


def _chunked(body: bytes) -> Generator[bytes, None, None]:
    """Yield the body in pieces: httpx sends an iterator without a length as
    ``Transfer-Encoding: chunked``, which is the case a Content-Length check
    alone cannot catch."""
    for start in range(0, len(body), _CHUNK):
        yield body[start : start + _CHUNK]


def test_transcribe_accepts_a_body_at_the_exact_limit(
    recording_client: tuple[TestClient, RecordingSpeechClient],
) -> None:
    client, speech = recording_client
    response = client.post(
        "/api/voice/transcribe",
        content=_upload_of_total_size(MAX_AUDIO_BYTES),
        headers={"content-type": _MULTIPART_TYPE},
    )
    assert response.status_code == 200
    assert speech.calls == ["transcribe"]


def test_transcribe_rejects_one_byte_over_the_limit(
    recording_client: tuple[TestClient, RecordingSpeechClient],
) -> None:
    client, speech = recording_client
    response = client.post(
        "/api/voice/transcribe",
        content=_upload_of_total_size(MAX_AUDIO_BYTES + 1),
        headers={"content-type": _MULTIPART_TYPE},
    )
    assert response.status_code == 413
    assert "too large" in response.json()["detail"]
    assert speech.calls == []  # nothing was forwarded upstream


def test_transcribe_rejects_a_chunked_upload_without_content_length(
    recording_client: tuple[TestClient, RecordingSpeechClient],
) -> None:
    client, speech = recording_client
    response = client.post(
        "/api/voice/transcribe",
        content=_chunked(_upload_of_total_size(MAX_AUDIO_BYTES + 1)),
        headers={"content-type": _MULTIPART_TYPE},
    )
    assert "content-length" not in response.request.headers
    assert response.request.headers["transfer-encoding"] == "chunked"
    assert response.status_code == 413
    assert speech.calls == []


def test_transcribe_accepts_a_chunked_upload_at_the_limit(
    recording_client: tuple[TestClient, RecordingSpeechClient],
) -> None:
    client, speech = recording_client
    response = client.post(
        "/api/voice/transcribe",
        content=_chunked(_upload_of_total_size(MAX_AUDIO_BYTES)),
        headers={"content-type": _MULTIPART_TYPE},
    )
    assert response.status_code == 200
    assert speech.calls == ["transcribe"]


def test_transcribe_rejects_an_oversized_declared_length(
    recording_client: tuple[TestClient, RecordingSpeechClient],
) -> None:
    """A truthful ``Content-Length`` is refused before the body is read."""
    client, speech = recording_client
    response = client.post(
        "/api/voice/transcribe",
        content=b"x" * 16,
        headers={
            "content-type": _MULTIPART_TYPE,
            "content-length": str(MAX_AUDIO_BYTES + 1),
        },
    )
    assert response.status_code == 413
    assert speech.calls == []


def test_speak_accepts_text_at_the_exact_limit(
    recording_client: tuple[TestClient, RecordingSpeechClient],
) -> None:
    client, speech = recording_client
    response = client.post(
        "/api/voice/speak", json={"text": "a" * MAX_SPEAK_TEXT_LENGTH}
    )
    assert response.status_code == 200
    assert speech.calls == ["speak"]


def test_speak_rejects_one_character_over_the_limit(
    recording_client: tuple[TestClient, RecordingSpeechClient],
) -> None:
    client, speech = recording_client
    response = client.post(
        "/api/voice/speak", json={"text": "a" * (MAX_SPEAK_TEXT_LENGTH + 1)}
    )
    assert response.status_code == 422
    assert speech.calls == []


def test_speak_rejects_an_oversized_body_before_validating_it(
    recording_client: tuple[TestClient, RecordingSpeechClient],
) -> None:
    """The byte ceiling covers the JSON side too: a megabyte-scale text field
    is refused as a payload rather than buffered whole and then 422'd."""
    client, speech = recording_client
    body = b'{"text": "' + b"a" * (MAX_AUDIO_BYTES + 1) + b'"}'
    response = client.post(
        "/api/voice/speak",
        content=body,
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 413
    assert speech.calls == []
