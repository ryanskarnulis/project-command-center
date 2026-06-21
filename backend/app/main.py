from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIRouter

from app.api import (
    routes_ai,
    routes_discord,
    routes_inbox,
    routes_projects,
    routes_search,
    routes_settings,
    routes_task_dependencies,
    routes_tasks,
    routes_today,
    routes_training,
    routes_trash,
)
from app.config import get_settings
from app.logging_config import RequestIDMiddleware, configure_logging

logger = structlog.get_logger(__name__)

api_router = APIRouter()
api_router.include_router(routes_projects.router)
api_router.include_router(routes_tasks.router)
api_router.include_router(routes_task_dependencies.router)
api_router.include_router(routes_inbox.router)
api_router.include_router(routes_discord.router)
api_router.include_router(routes_ai.router)
api_router.include_router(routes_settings.router)
api_router.include_router(routes_training.router)
api_router.include_router(routes_trash.router)
api_router.include_router(routes_search.router)
api_router.include_router(routes_today.router)


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


@app.get("/health")
async def health() -> dict[str, str]:
    logger.info("health_check")
    return {"status": "ok", "env": get_settings().app_env}


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app", host=get_settings().api_host, port=8000, reload=True
    )
