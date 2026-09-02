"""Seed data loading and demo replay.

The seed set is synthetic. The messages are written by hand to resemble
published flood reporting from Nowshera district; no real person, phone number
or address appears anywhere in this repository. The console says so on screen
and the README says so too.

Replay pushes the seed messages through `PipelineService.accept` -- the same
entry point live intake uses. There is no separate demo code path and no
scripted outcome: the two rescue calls contend for the one boat because there
genuinely is only one boat in resources.json.
"""

from __future__ import annotations

import json
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

from ..models.common import utcnow
from ..models.request import Channel, HelpRequest
from ..models.resource import Resource
from ..store.requests_repo import RequestsRepo
from ..store.resources_repo import ResourcesRepo
from .events import get_bus

SEED_DIR = Path(__file__).resolve().parents[3] / "seed"


def load_seed_requests() -> list[dict[str, Any]]:
    path = SEED_DIR / "requests.jsonl"
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def load_seed_resources() -> list[Resource]:
    path = SEED_DIR / "resources.json"
    if not path.is_file():
        return []
    return [Resource.model_validate(r) for r in json.loads(path.read_text(encoding="utf-8"))]


def install_resources() -> int:
    """Write the roster. Idempotent."""
    repo = ResourcesRepo()
    resources = load_seed_resources()
    for resource in resources:
        repo.save(resource)
    return len(resources)


def reset() -> dict[str, int]:
    """Clear every request, decision and audit row, and reinstall the roster.

    The geocode cache is deliberately kept: it is expensive to rebuild, contains
    nothing about any person, and keeping it is what makes replay offline-safe.
    """
    from ..store.table import get_table

    table = get_table()
    removed = 0
    for prefix in ("REQ#", "DEC#", "AUDIT#"):
        for item in table.backend.scan_prefix(prefix):
            table.backend.delete(item["pk"], item["sk"])
            removed += 1

    count = install_resources()
    get_bus().publish({"type": "demo_reset", "resources": count, "removed": removed})
    return {"removed": removed, "resources": count}


def location_queries() -> list[str]:
    """Every place name in the seed set, for pre-warming the geocode cache."""
    seen: list[str] = []
    for row in load_seed_requests():
        for token in _place_guesses(str(row.get("text", ""))):
            if token not in seen:
                seen.append(token)
    return seen


# Village and town names that appear in the seed messages. Listed explicitly
# rather than guessed at, so `warm_cache` resolves exactly these and the demo
# never waits on Nominatim mid-replay.
KNOWN_PLACES = [
    "Nowshera", "Nowshera Kalan", "Nowshera Cantt", "Mohib Banda", "Pir Sabaq",
    "Kheshgi Payan", "Kheshgi Bala", "Pabbi", "Akora Khattak", "Risalpur",
    "Jehangira", "Dag Ismail Khel", "Badrashi", "Manki Sharif", "Zarobi",
    "Tarakai", "Aman Kot",
]


def _place_guesses(text: str) -> list[str]:
    return [place for place in KNOWN_PLACES if place.casefold() in text.casefold()]


def warm_geocache() -> dict[str, int]:
    from ..agent.tools.geocode import warm_cache

    return warm_cache(KNOWN_PLACES)


def replay(speed: float = 1.0, limit: int | None = None) -> dict[str, Any]:
    """Push the seed messages through the live intake path.

    `speed` is a multiplier on the gap between messages: 0 means "as fast as the
    pipeline will take them", 1 means roughly one every few hundred milliseconds.
    """
    from .pipeline import get_pipeline_service

    service = get_pipeline_service()
    rows = load_seed_requests()[: limit or None]
    install_resources()

    accepted: list[str] = []
    base = utcnow()
    repo = RequestsRepo()

    for index, row in enumerate(rows):
        channel: Channel = row.get("channel", "form")
        request = service.accept(
            str(row["text"]), channel=channel, photo_key=row.get("photo")
        )
        # Preserve the seed's relative timing so recency and the dedupe window
        # behave as they would have on the night.
        offset = float(row.get("offset_min", index))
        request.received_at = base - timedelta(minutes=max(0.0, 240 - offset))
        repo.save(request)

        service.submit(request)
        accepted.append(request.id)

        if speed > 0:
            time.sleep(min(2.0, 0.25 / max(speed, 0.01)))

    get_bus().publish({"type": "replay_started", "count": len(accepted)})
    return {"accepted": len(accepted), "request_ids": accepted}


def seed_summary() -> dict[str, Any]:
    """What the About screen shows about the demo data."""
    rows = load_seed_requests()
    return {
        "synthetic": True,
        "requests": len(rows),
        "resources": len(load_seed_resources()),
        "note": (
            "Synthetic requests modelled on published flood reporting. No real people, "
            "phone numbers or addresses appear in this dataset."
        ),
    }


def as_help_requests() -> list[HelpRequest]:
    """The seed rows as unprocessed requests, for tests that need a fixed set."""
    from ..agent.nodes import intake

    return [
        intake.build(str(row["text"]), channel=row.get("channel", "form"), request_id=row["id"])
        for row in load_seed_requests()
    ]
