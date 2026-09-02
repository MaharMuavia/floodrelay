"""Shared fixtures.

The whole unit suite runs against the in-memory backend: no Docker, no network,
no AWS. Anything that genuinely needs DynamoDB Local lives in the e2e script.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

# Set before any floodrelay import so Settings picks these up.
os.environ.setdefault("DDB_ENDPOINT", "memory")
# Unit tests never construct a model; the provider is irrelevant here.
os.environ.setdefault("DEMO_MODE", "true")

from floodrelay.models import (
    Confidence,
    ExtractedNeed,
    HelpRequest,
    Resource,
)
from floodrelay.store import (
    AuditRepo,
    DecisionsRepo,
    GeoCacheRepo,
    RequestsRepo,
    ResourcesRepo,
    Table,
)
from floodrelay.store.table import MemoryBackend, reset_table

FIXED_NOW = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def table() -> Iterator[Table]:
    t = Table(MemoryBackend())
    reset_table(t)
    yield t
    reset_table(None)


@pytest.fixture
def requests_repo(table: Table) -> RequestsRepo:
    return RequestsRepo(table)


@pytest.fixture
def resources_repo(table: Table) -> ResourcesRepo:
    return ResourcesRepo(table)


@pytest.fixture
def decisions_repo(table: Table) -> DecisionsRepo:
    return DecisionsRepo(table)


@pytest.fixture
def audit_repo(table: Table) -> AuditRepo:
    return AuditRepo(table)


@pytest.fixture
def geocache(table: Table) -> GeoCacheRepo:
    return GeoCacheRepo(table)


def make_need(**overrides: object) -> ExtractedNeed:
    base: dict[str, object] = {
        "kind": "rescue",
        "extraction_confidence": Confidence(score=0.9, reason="explicit in message"),
    }
    base.update(overrides)
    return ExtractedNeed.model_validate(base)


def make_request(request_id: str = "r_1", **overrides: object) -> HelpRequest:
    base: dict[str, object] = {
        "id": request_id,
        "channel": "whatsapp",
        "raw_text": "water rising, need help",
        "received_at": FIXED_NOW,
    }
    base.update(overrides)
    return HelpRequest.model_validate(base)


def make_resource(resource_id: str = "res_boat_1", **overrides: object) -> Resource:
    base: dict[str, object] = {
        "id": resource_id,
        "name": "Rescue boat 1",
        "kind": "boat",
        "capabilities": ["water_rescue"],
        "capacity": 8,
        "lat": 34.0151,
        "lon": 71.9747,
    }
    base.update(overrides)
    return Resource.model_validate(base)
