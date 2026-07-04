# syntax=docker/dockerfile:1
#
# Backend image for the docker-compose deployment. Also reused verbatim by the
# discord-bot service (same package + deps, different entrypoint command).
# Build context is the repo root, so source paths are prefixed with backend/.

FROM python:3.11-slim

# All runtime deps ship as wheels (fastapi, pydantic-core, uvicorn, …), so no
# compiler/toolchain layer is needed.
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # Absolute path onto the mounted /data volume. Both the app and Alembic read
    # DATABASE_URL, so this single var points them at the persisted SQLite file.
    DATABASE_URL=sqlite:////data/app.db

# Alembic's script_location is cwd-relative; run everything from here.
WORKDIR /app/backend

# requirements.lock pins every version; -e keeps the source importable as `app`.
# No `[dev]` extra → pytest/mypy/ruff are excluded from the runtime image.
COPY backend/ ./
RUN pip install -e . -c requirements.lock

EXPOSE 8000

# Apply migrations, then serve. Single worker (no --reload) keeps the in-process
# per-IP rate limiter correct. Bind 0.0.0.0 so the separate nginx container can
# reach us over the compose network — this service publishes no host port.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
