"""PCC's voice vocabulary and settings wiring over the fleet speech client.

The client itself — the OpenAI-audio wire calls, typed errors, factory — is
the vendored ``speech_client`` (canonical in ``../speech/client/python/``, per
the fleet voice contract ``../agent-standard/voice.md``). This module holds
what is PCC's own: the whisper prompt, bundled as ``VOCABULARY``, and the
settings-driven factory.
"""

from __future__ import annotations

from app.ai.speech_client import SpeechClient, Vocabulary, create_speech_client
from app.config import get_settings

# Whisper conditions on this prompt as if it preceded the audio, biasing
# recognition toward PCC's vocabulary and mimicking its formatting. Required
# by the voice standard; changes gate on the agent eval harness (the prompt
# shapes what text enters the same /api/agent pipeline the evals exercise).
STT_PROMPT = (
    "Project and task commands: add a task to buy milk tomorrow, create a "
    "project called spring cleaning, what's due Friday, what's overdue, move "
    "it to focus, mark it done, set the priority to high, plan my day, show "
    "my tasks for this week, delete the task, put it in the backlog."
)


VOCABULARY = Vocabulary(stt_prompt=STT_PROMPT)


def speech_client_from_settings() -> SpeechClient | None:
    """Build a ``SpeechClient`` from settings, or ``None`` when voice is off.

    Voice is optional exactly like the agent: no ``SPEECH_BASE_URL`` means no
    client and the voice endpoints answer 503; the rest of the app is
    untouched."""
    settings = get_settings()
    if not settings.speech_base_url:
        return None
    return create_speech_client(
        base_url=settings.speech_base_url,
        vocabulary=VOCABULARY,
        tts_base_url=settings.tts_base_url,
        stt_model=settings.stt_model,
        tts_model=settings.tts_model,
        tts_voice=settings.tts_voice,
    )
