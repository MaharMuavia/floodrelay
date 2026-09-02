"""Shared HTTP plumbing for the tool layer.

Two rules hold for every outbound call:

1. A tool never raises into the agent loop. Failures come back as typed values
   the model can read and reason about ("the geocoder timed out") rather than
   as exceptions that kill the graph run mid-flight.
2. Every service that publishes a usage policy gets that policy enforced here,
   in code, not in a prompt. Nominatim's one-request-per-second limit is the
   main one; a rate limiter the model can talk its way past is not a rate
   limiter.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

import httpx

from ...config import get_settings


class RateLimiter:
    """Process-wide minimum interval between calls to one service.

    Blocking rather than async-yielding on purpose: the whole point is that no
    amount of concurrency upstream can produce two requests inside the window.
    """

    def __init__(self, min_interval_s: float) -> None:
        self.min_interval_s = min_interval_s
        self._lock = threading.Lock()
        self._last = 0.0

    def acquire(self) -> float:
        """Block until it is legal to call again. Returns seconds waited."""
        with self._lock:
            now = time.monotonic()
            wait = self.min_interval_s - (now - self._last)
            if wait > 0:
                time.sleep(wait)
                self._last = time.monotonic()
                return wait
            self._last = now
            return 0.0


# Nominatim's published policy: absolute maximum of one request per second.
nominatim_limiter = RateLimiter(1.0)
# Overpass asks for restraint rather than naming a number; two seconds is polite.
overpass_limiter = RateLimiter(2.0)


@dataclass(frozen=True)
class HttpResult:
    """A request outcome the agent can read without try/except."""

    ok: bool
    status: int | None = None
    data: Any = None
    error: str | None = None

    @classmethod
    def failure(cls, error: str, status: int | None = None) -> HttpResult:
        return cls(ok=False, status=status, error=error)


def user_agent() -> str:
    """Descriptive UA with a contact address, as Nominatim's policy requires."""
    return get_settings().nominatim_user_agent


def get_json(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    timeout: float = 10.0,
    limiter: RateLimiter | None = None,
    headers: dict[str, str] | None = None,
) -> HttpResult:
    """GET and parse JSON, converting every failure mode into a value."""
    if limiter is not None:
        limiter.acquire()
    merged = {"User-Agent": user_agent(), "Accept": "application/json"}
    if headers:
        merged.update(headers)
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.get(url, params=params, headers=merged)
    except httpx.TimeoutException:
        return HttpResult.failure(f"timed out after {timeout}s")
    except httpx.HTTPError as exc:
        return HttpResult.failure(f"network error: {exc.__class__.__name__}: {exc}")

    if resp.status_code >= 400:
        return HttpResult.failure(
            f"HTTP {resp.status_code} from {resp.request.url.host}", status=resp.status_code
        )
    try:
        return HttpResult(ok=True, status=resp.status_code, data=resp.json())
    except ValueError:
        return HttpResult.failure("response was not valid JSON", status=resp.status_code)


def post_text(
    url: str, *, content: str, timeout: float = 25.0, limiter: RateLimiter | None = None
) -> HttpResult:
    """POST a raw body (Overpass QL) and parse the JSON response."""
    if limiter is not None:
        limiter.acquire()
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.post(
                url,
                content=content.encode("utf-8"),
                headers={"User-Agent": user_agent(), "Content-Type": "text/plain"},
            )
    except httpx.TimeoutException:
        return HttpResult.failure(f"timed out after {timeout}s")
    except httpx.HTTPError as exc:
        return HttpResult.failure(f"network error: {exc.__class__.__name__}: {exc}")

    if resp.status_code >= 400:
        return HttpResult.failure(f"HTTP {resp.status_code}", status=resp.status_code)
    try:
        return HttpResult(ok=True, status=resp.status_code, data=resp.json())
    except ValueError:
        return HttpResult.failure("response was not valid JSON", status=resp.status_code)
