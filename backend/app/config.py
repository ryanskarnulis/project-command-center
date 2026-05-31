from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    app_env: str = "development"
    database_url: str = "sqlite:///../data/app.db"
    ollama_base_url: str = "http://localhost:11434"

    # Host the API binds to. Constitution default is loopback-only; set to
    # "0.0.0.0" in .env to expose the API on the LAN.
    api_host: str = "127.0.0.1"

    # Discord integration (Sprint 3). The bot is a separate process that calls the
    # API over HTTP. The shared secret is the real protection on the discord route
    # (an empty secret disables the route); compared constant-time on every request.
    backend_shared_secret: str = ""
    discord_bot_token: str = ""
    # Where the bot process reaches the API (loopback by default — same host).
    backend_base_url: str = "http://127.0.0.1:8000"

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
