"""Server-sent events: the live agent activity feed."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from ..services.events import get_bus

router = APIRouter(tags=["stream"])


@router.get("/stream")
async def stream(
    request: Request, replay: int = Query(default=20, ge=0, le=200)
) -> StreamingResponse:
    """Stream agent events as they happen.

    Sends a heartbeat every 15 seconds. Bad wifi is the operating condition, and
    an idle connection with no traffic is one a proxy will quietly close.
    """
    bus = get_bus()

    async def generate() -> AsyncIterator[bytes]:
        yield b": connected\n\n"
        async for event in bus.stream(replay=replay):
            if await request.is_disconnected():
                break
            payload = json.dumps(event, default=str, separators=(",", ":"))
            yield f"event: {event.get('type', 'message')}\ndata: {payload}\n\n".encode()

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
