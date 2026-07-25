from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
import uvicorn
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.routing import APIRouter
from sqlalchemy.exc import IntegrityError

from app.api import (
    routes_agent,
    routes_dashboard,
    routes_focus,
    routes_projects,
    routes_search,
    routes_task_dependencies,
    routes_tasks,
    routes_trash,
    routes_voice,
)
from app.config import get_settings
from app.logging_config import RequestIDMiddleware, configure_logging

logger = structlog.get_logger(__name__)

api_router = APIRouter()
api_router.include_router(routes_projects.router)
api_router.include_router(routes_tasks.router)
api_router.include_router(routes_task_dependencies.router)
api_router.include_router(routes_dashboard.router)
api_router.include_router(routes_trash.router)
api_router.include_router(routes_search.router)
api_router.include_router(routes_focus.router)
api_router.include_router(routes_agent.router)
api_router.include_router(routes_voice.router)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    configure_logging()
    logger.info("startup", env=get_settings().app_env)
    yield


app = FastAPI(lifespan=lifespan)
app.add_middleware(RequestIDMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_origin_regex=get_settings().cors_origin_regex,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(api_router, prefix="/api")


@app.exception_handler(IntegrityError)
async def integrity_conflict(
    request: Request, exc: IntegrityError
) -> JSONResponse:
    """A database constraint that reached the transport is a conflict, not a crash.

    Services translate the constraints they know about into domain errors with
    specific messages (``DuplicateDependencyError``,
    ``OccurrenceConflictError``); this is the net under the ones nobody thought
    about. Without it a violation returns 500 with a stack trace, and this app is
    deliberately reachable over the LAN. The detail stays generic on purpose —
    index names are schema detail, not something to hand to a client — while the
    log line keeps the driver message for whoever is debugging.
    """
    logger.warning(
        "integrity_error",
        path=request.url.path,
        method=request.method,
        error=str(exc.orig),
    )
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={"detail": "That change conflicts with the current state of the data."},
    )


@app.get("/health")
async def health() -> dict[str, str]:
    logger.info("health_check")
    return {"status": "ok", "env": get_settings().app_env}


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=get_settings().api_host,
        port=get_settings().api_port,
        reload=True,
    )
