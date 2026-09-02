"""The live event bus behind GET /stream.

One in-process broker with a bounded queue per subscriber. Bounded on purpose:
a coordinator on bad wifi whose browser stops reading must not be able to grow
the server's memory without limit. When a subscriber falls too far behind, its
oldest events are dropped and it is told so, which is the honest behaviour for a
console whose actual operating condition is a poor connection.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import deque
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

from ..models.common import utcnow

MAX_QUEUED = 500


class Subscriber:
    def __init__(self, limit: int = MAX_QUEUED) -> None:
        self.queue: deque[dict[str, Any]] = deque(maxlen=limit)
        self.wakeup = asyncio.Event()
        self.dropped = 0

    def push(self, event: dict[str, Any]) -> None:
        if len(self.queue) == self.queue.maxlen:
            self.dropped += 1
        self.queue.append(event)
        self.wakeup.set()


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[Subscriber] = set()
        self._recent: deque[dict[str, Any]] = deque(maxlen=200)
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Remember the API's loop so worker threads can publish into it."""
        self._loop = loop

    def publish(self, event: dict[str, Any]) -> None:
        """Thread-safe. The pipeline runs in a worker thread; the API does not."""
        enriched = {**event, "ts": event.get("ts") or utcnow().isoformat()}
        self._recent.append(enriched)

        if self._loop is not None and not self._loop.is_closed():
            try:
                self._loop.call_soon_threadsafe(self._fanout, enriched)
                return
            except RuntimeError:
                pass
        self._fanout(enriched)

    def _fanout(self, event: dict[str, Any]) -> None:
        for sub in list(self._subscribers):
            sub.push(event)

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        return list(self._recent)[-limit:]

    @contextlib.contextmanager
    def subscribe(self):  # type: ignore[no-untyped-def]
        sub = Subscriber()
        self._subscribers.add(sub)
        try:
            yield sub
        finally:
            self._subscribers.discard(sub)

    async def stream(self, replay: int = 20) -> AsyncIterator[dict[str, Any]]:
        """Yield events until the client disconnects."""
        with self.subscribe() as sub:
            for event in self.recent(replay):
                yield event
            while True:
                if not sub.queue:
                    sub.wakeup.clear()
                    try:
                        await asyncio.wait_for(sub.wakeup.wait(), timeout=15.0)
                    except TimeoutError:
                        # Keepalive: proxies and phones drop idle connections.
                        yield {"type": "heartbeat", "ts": utcnow().isoformat()}
                        continue
                while sub.queue:
                    yield sub.queue.popleft()
                if sub.dropped:
                    yield {
                        "type": "events_dropped",
                        "count": sub.dropped,
                        "ts": utcnow().isoformat(),
                    }
                    sub.dropped = 0

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


_bus = EventBus()


def get_bus() -> EventBus:
    return _bus


def iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
