"""Voice API: STT/TTS proxies to the shared workspace speech service.

The browser posts audio here and gets text back (``/voice/transcribe``), or
posts text and gets mp3 back (``/voice/speak``); the transcript then goes
down the exact same ``/agent`` pipeline as typed input — voice never gets its
own path to the data. Both endpoints are rate-limited per client IP like the
agent surface, answer 503 when no speech service is configured, and 502 when
the upstream fails. Nothing here touches the database.

Both also carry explicit payload limits (``MAX_AUDIO_BYTES`` → 413,
``MAX_SPEAK_TEXT_LENGTH`` → 422), so the size a request may be is part of the
API contract rather than an accident of whichever proxy happens to sit in
front — the app is reachable directly over the LAN, with no proxy at all.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine, Generator
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Response, UploadFile, status
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field, field_validator
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.types import Message, Receive

from app.ai.speech import SpeechClient, SpeechError, speech_client_from_settings
from app.api.rate_limit import rate_limit
from app.schemas.common import MutationModel

logger = structlog.get_logger(__name__)

# --- payload limits -----------------------------------------------------------
#
# The rate limiter counts requests, not bytes, so without these one request can
# make the backend buffer an arbitrarily large upload and forward it upstream
# (#245). Sized from what the browser actually records:
#
#   * hands-free (the usual path) posts 16 kHz mono 16-bit PCM WAV from the VAD
#     (`frontend/src/voice/wav.ts`) — 16000 x 2 = 32,000 bytes per second;
#   * push-to-talk posts MediaRecorder webm/opus, ~4 KB/s — an order of
#     magnitude smaller, so WAV sets the size.
#
# Treating 120 s as the longest utterance worth supporting: 120 x 32,000 =
# 3,840,000 bytes (~3.7 MiB). The ceiling is 8 MiB — 262 s of that WAV, i.e.
# better than 2x headroom, and it bounds the whole multipart request rather
# than just the audio part, so the envelope is covered too.
MAX_AUDIO_BYTES = 8 * 1024 * 1024

# TTS text is an agent reply read aloud. At ~13 characters of English per
# spoken second, 2,000 characters is ~2.5 minutes of audio: far longer than any
# reply the chat panel voices, and still synthesizable inside the speech
# client's 60 s read timeout (8,000 — the agent *message* cap — would not be).
MAX_SPEAK_TEXT_LENGTH = 2_000


class _PayloadTooLarge(Exception):
    """Raised mid-stream once a request body crosses ``MAX_AUDIO_BYTES``."""


class _BoundedBody:
    """Counts an ASGI request body as it arrives and cuts it off at ``limit``.

    The count has to happen chunk by chunk to bound *memory*: reading the whole
    body and measuring it afterwards has already paid the cost this guards
    against. Nothing beyond the ceiling is ever buffered — the chunk that
    crosses it is dropped, unread bytes are never pulled, and the body parser
    downstream never runs, so the route handler (and the speech service behind
    it) never sees oversized input.

    The raise is what guarantees that, but it does not survive as itself:
    FastAPI wraps *any* failure during body parsing into its own 400/422. So
    ``exceeded`` records the reason, and the route handler below reads the flag
    to convert whatever FastAPI produced into the real 413.
    """

    def __init__(self, receive: Receive, limit: int) -> None:
        self._receive = receive
        self._limit = limit
        self.received = 0
        self.exceeded = False

    async def receive(self) -> Message:
        message = await self._receive()
        if message["type"] == "http.request":
            self.received += len(message.get("body", b""))
            if self.received > self._limit:
                self.exceeded = True
                raise _PayloadTooLarge
        return message


class BoundedBodyRoute(APIRoute):
    """Route class that answers 413 instead of buffering an oversized body.

    FastAPI parses the request body *before* dependencies run, so a dependency
    cannot get in front of it — the guard has to wrap the route handler and
    hand it a request whose stream is already capped. ``Content-Length`` is
    only an early exit: a chunked upload declares no length at all, so the
    streaming count is what actually enforces the ceiling.
    """

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def bounded(request: Request) -> Response:
            declared = request.headers.get("content-length")
            if declared is not None and declared.isdigit():
                if int(declared) > MAX_AUDIO_BYTES:
                    return _too_large(request, int(declared), streamed=False)
            guard = _BoundedBody(request.receive, MAX_AUDIO_BYTES)
            try:
                response = await original(Request(request.scope, guard.receive))
            except Exception:
                if guard.exceeded:
                    return _too_large(request, guard.received, streamed=True)
                raise
            if guard.exceeded:
                return _too_large(request, guard.received, streamed=True)
            return response

        return bounded


def _too_large(request: Request, received: int, *, streamed: bool) -> JSONResponse:
    """The 413, logged by size only — never audio bytes, never text."""
    logger.warning(
        "voice_payload_too_large",
        path=request.url.path,
        limit_bytes=MAX_AUDIO_BYTES,
        received_bytes=received,
        streamed=streamed,
    )
    return JSONResponse(
        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
        content={
            "detail": (
                f"payload too large: the voice endpoints accept at most "
                f"{MAX_AUDIO_BYTES} bytes per request"
            )
        },
    )


router = APIRouter(
    prefix="/voice",
    tags=["voice"],
    route_class=BoundedBodyRoute,
    dependencies=[Depends(rate_limit("voice", per_min_attr="voice_requests_per_min"))],
)


def get_speech_client() -> Generator[SpeechClient | None, None, None]:
    """The client per settings (None = voice off); tests override this."""
    speech = speech_client_from_settings()
    try:
        yield speech
    finally:
        if speech is not None:
            speech.close()


def _require(speech: SpeechClient | None) -> SpeechClient:
    if speech is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="voice unavailable: no speech service configured",
        )
    return speech


class SpeakRequest(MutationModel):
    # Bounded here rather than at the transport: an over-long utterance is a
    # contract violation with a documented 422, not a proxy-shaped failure.
    text: str = Field(max_length=MAX_SPEAK_TEXT_LENGTH)

    @field_validator("text")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must not be blank")
        return value


class TranscriptionResult(BaseModel):
    text: str


@router.post(
    "/transcribe",
    response_model=TranscriptionResult,
    responses={413: {"description": f"audio above {MAX_AUDIO_BYTES} bytes"}},
)
async def transcribe(
    audio: UploadFile,
    speech: SpeechClient | None = Depends(get_speech_client),
) -> TranscriptionResult:
    """STT proxy: browser audio in, plain text out.

    The client feeds the text into the same agent pipeline as typed input;
    this endpoint itself reads and writes nothing.

    ``SpeechClient.transcribe`` is synchronous httpx and can block for the
    full upstream timeout, so it runs in a worker thread — an in-flight STT
    round trip must not stall the event loop for every other request."""
    client = _require(speech)
    # Bounded by ``BoundedBodyRoute``: the request stream is cut off at
    # ``MAX_AUDIO_BYTES`` before the multipart parser ever sees the excess, so
    # this read cannot be larger than that.
    data = await audio.read()
    try:
        text = await run_in_threadpool(
            client.transcribe, data, filename=audio.filename or "audio.webm"
        )
    except SpeechError as exc:
        logger.error("voice_transcribe_failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"speech service error: {exc}",
        ) from exc
    logger.info("voice_transcribed", audio_bytes=len(data), text_length=len(text))
    return TranscriptionResult(text=text)


@router.post(
    "/speak",
    responses={
        413: {"description": f"body above {MAX_AUDIO_BYTES} bytes"},
        422: {"description": f"text blank or above {MAX_SPEAK_TEXT_LENGTH} characters"},
    },
)
def speak(
    request: SpeakRequest,
    speech: SpeechClient | None = Depends(get_speech_client),
) -> Response:
    """TTS proxy: text in, mp3 out — the audio for whatever reply the client
    decided to voice (voice in → voice out; typed turns stay silent)."""
    client = _require(speech)
    try:
        audio = client.speak(request.text)
    except SpeechError as exc:
        logger.error("voice_speak_failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"speech service error: {exc}",
        ) from exc
    logger.info("voice_spoken", text_length=len(request.text), audio_bytes=len(audio))
    return Response(content=audio, media_type="audio/mpeg")
