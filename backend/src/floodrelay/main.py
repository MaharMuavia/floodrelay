"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import (
    routes_agentcore,
    routes_audit,
    routes_board,
    routes_context,
    routes_decisions,
    routes_demo,
    routes_intake,
    routes_internal,
    routes_stream,
)
from .api.deps import Health
from .config import get_settings
from .services.events import get_bus
from .telemetry import setup_tracing

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
    settings = get_settings()
    app.state.tracing = setup_tracing(settings)

    # Worker threads publish SSE events into the API's loop.
    get_bus().bind_loop(asyncio.get_running_loop())

    from .services.seed import install_resources
    from .store.table import get_table

    table = get_table(settings)
    app.state.store_label = table.label
    installed = install_resources()
    logger.info("FloodRelay ready: store=%s resources=%d", table.label, installed)

    yield


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="FloodRelay",
        version="0.1.0",
        summary="Flood-relief coordination agent with a hard human gate on dispatch.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    for module in (
        routes_intake,
        routes_board,
        routes_context,
        routes_decisions,
        routes_stream,
        routes_audit,
        routes_demo,
        routes_internal,
        routes_agentcore,
    ):
        app.include_router(module.router)

    @app.get("/healthz", response_model=Health, tags=["ops"])
    def healthz() -> Health:
        from .agent.models import describe_models
        from .services.media import backend_label
        from .store.table import get_table

        checks: dict[str, Any] = {}
        try:
            table = get_table()
            table.backend.scan_prefix("RES#", limit=1)
            checks["store"] = "ok"
        except Exception as exc:
            checks["store"] = f"error: {exc.__class__.__name__}"

        checks["media"] = backend_label()
        checks["tracing"] = getattr(app.state, "tracing", "unknown")
        checks["stream_subscribers"] = get_bus().subscriber_count

        # Whether a Strands Agent is choosing and calling the @tool functions,
        # or the pipeline is calling them from Python around a completion-only
        # model. Reported rather than assumed: it is the difference between two
        # genuinely different systems and an operator should not have to read
        # the config to find out which one they are running.
        from .agent.models import tool_calling_active

        checks["tool_calling"] = (
            "active: the model chooses and calls tools"
            if tool_calling_active()
            else "inactive: tools are called from Python around the model"
        )

        from .agent.tools.imagery import vision_available

        checks["photo_severity"] = (
            "available" if vision_available() else "unavailable: the configured model has no vision"
        )

        return Health(
            status="ok" if checks.get("store") == "ok" else "degraded",
            store=get_table().label,
            models=describe_models(),
            demo_mode=settings.demo_mode,
            checks=checks,
        )

    @app.get("/", tags=["ops"])
    def root() -> dict[str, str]:
        return {
            "name": "FloodRelay",
            "docs": "/docs",
            "console": "the Next.js console runs separately; see the README",
        }

    return app


app = create_app()
