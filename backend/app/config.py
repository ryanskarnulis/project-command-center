from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # env_ignore_empty: an empty env var (e.g. DISCORD_GUILD_ID= in a compose
    # .env) is treated as unset and falls back to the default, rather than being
    # parsed as "" — which would crash a typed-optional field like the guild id.
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", env_ignore_empty=True
    )

    app_env: str = "development"
    database_url: str = "sqlite:///../data/app.db"
    ollama_base_url: str = "http://localhost:11434"

    # Host the API binds to. Constitution default is loopback-only; set to
    # "0.0.0.0" in .env to expose the API on the LAN. Settings read routes are
    # visible over the LAN, but settings writes are guarded localhost-only.
    api_host: str = "127.0.0.1"

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

    # Discord integration (Sprint 3). The bot is a separate process that calls the
    # API over HTTP. The shared secret is the real protection on the discord route
    # (an empty secret disables the route); compared constant-time on every request.
    backend_shared_secret: str = ""
    discord_bot_token: str = ""
    # Where the bot process reaches the API (loopback by default — same host).
    backend_base_url: str = "http://127.0.0.1:8000"
    # Optional: sync slash commands to this one guild for instant availability
    # during testing. Global sync (when unset) can take up to ~an hour to appear.
    discord_guild_id: int | None = None

    # Per-IP rate limits (requests/min) on the two routes that call Ollama, to
    # cap runaway model work before wider LAN exposure. Tune via .env if needed.
    rate_limit_discord_inbox_per_min: int = 30
    rate_limit_summary_per_min: int = 20
    rate_limit_inbox_process_per_min: int = 20
    rate_limit_breakdown_per_min: int = 20

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
