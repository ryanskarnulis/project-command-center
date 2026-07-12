from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # env_ignore_empty: an empty env var (e.g. a blank optional value in a
    # compose .env) is treated as unset and falls back to the default, rather
    # than being parsed as "" — which would crash a typed-optional field.
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", env_ignore_empty=True
    )

    app_env: str = "development"
    database_url: str = "sqlite:///../data/app.db"

    # Host the API binds to. Constitution default is loopback-only; set to
    # "0.0.0.0" in .env to expose the API on the LAN. Settings read routes are
    # visible over the LAN, but settings writes are guarded localhost-only.
    api_host: str = "127.0.0.1"

    # Port the dev API binds to (`python -m app.main`). 8101 sits in PCC's
    # 8100-8199 workspace block (8100 is the docker-published dashboard);
    # 8000 now belongs to the chess app. The docker image binds its own
    # container-internal 8000 in the Dockerfile CMD, independent of this.
    api_port: int = 8101

    # Reverse-proxy trust list for the write-guard and per-IP rate limiter.
    # Comma-separated IPs/CIDRs (e.g. "172.28.0.0/16"). Empty (default) = trust
    # nothing: the direct TCP peer is always the client, exactly as a direct bind.
    # Set this only to a proxy you control (the nginx container).
    trusted_proxy_ips: str = ""

    # How the dashboard's published port is bound in the docker deployment. Loopback
    # (the default) means only the host can reach the proxy, so the write-guard
    # accepts Settings writes forwarded by the trusted proxy. Set to "0.0.0.0" (via
    # FRONTEND_BIND) to expose the dashboard on the LAN — that automatically re-guards
    # Settings writes to loopback/exec clients only. See api/request_ip.py.
    frontend_bind: str = "127.0.0.1"

    # OpenAI-compatible base URL of the shared llama-swap runtime
    # (../llama-swap/, the workspace-level owner of the GPU — see
    # docs/agent-design.md "Runtime"). Dev default reaches it on the host
    # loopback; the docker deployment overrides this with host.docker.internal
    # (see docker-compose.yml's extra_hosts stanza).
    llamacpp_base_url: str = "http://127.0.0.1:8200/v1"
    llamacpp_model: str = "gemma-4-12b"
    # Per-request read timeout. Generous because a cold model load through
    # llama-swap is ~100 s before the first byte; warm calls never get near it.
    llamacpp_timeout_seconds: float = 300.0

    # Shared workspace speech service (../speech/: Speaches STT on 8400,
    # Kokoro-FastAPI TTS on 8410). Env var names are the fleet voice
    # contract's (../agent-standard/voice.md). Unset speech_base_url = voice
    # off: /api/voice endpoints answer 503, everything else untouched.
    speech_base_url: str | None = None
    # TTS on its own server (the house-voice Kokoro container); unset means
    # speech_base_url serves both STT and TTS.
    tts_base_url: str | None = None
    stt_model: str = "Systran/faster-whisper-small"
    tts_model: str = "speaches-ai/Kokoro-82M-v1.0-ONNX"
    tts_voice: str = "af_heart"

    # Per-IP cap on the /voice endpoints, rate-limited like the agent surface.
    # STT/TTS round-trips are cheap CPU work but proxy to a shared service;
    # 30/min covers a lively hands-free conversation with headroom.
    voice_requests_per_min: int = 30

    # Per-IP cap on POST /agent/conversations/{id}/messages — the one endpoint
    # that runs the model. A loop run takes seconds-to-minutes on the local
    # GPU, so 10/min is generous for a person and a brake on runaway clients.
    agent_messages_per_min: int = 10

    # Explicit CORS allow-list (the local Vite dev server).
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]
    # Also allow the Vite dev server when loaded from a private-LAN address
    # (host IP may change via DHCP, so match the range rather than a fixed IP).
    cors_origin_regex: str | None = (
        r"http://(localhost|127\.0\.0\.1"
        r"|10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
        r"|192\.168\.\d{1,3}\.\d{1,3}"
        r"|172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}):5173"
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
