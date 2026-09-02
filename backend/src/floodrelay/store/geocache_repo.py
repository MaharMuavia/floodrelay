"""Permanent geocode cache.

Nominatim's usage policy allows one request per second and expects results to be
cached rather than re-fetched. This repo is the reason the demo can replay forty
requests without touching the network: every lookup is checked here first, and
every result is written here forever.

Cache entries are never evicted. A place name that resolved yesterday resolves
to the same point today, and a flood console should not be at the mercy of a
rate limiter.
"""

from __future__ import annotations

import re
import unicodedata

from ..models.common import GeoCandidate
from .table import Table, geo_pk, get_table

# Bump when the geocoding request changes shape -- language, viewbox, filters.
# Entries are never evicted, so an old answer would otherwise outlive the
# question that produced it. v2: Accept-Language=en and a district viewbox.
CACHE_VERSION = "v2"

_PUNCT = re.compile(r"[^\w\s]", flags=re.UNICODE)
_SPACE = re.compile(r"\s+")


def normalise(query: str) -> str:
    """Fold a free-text place string to a stable cache key.

    Case, accents, punctuation and whitespace are all noise here: "Near the Old
    Bridge, Nowshera" and "near the old bridge nowshera" must hit the same entry,
    or the cache silently stops working and the rate limiter starts biting.
    """
    folded = unicodedata.normalize("NFKD", query.strip().casefold())
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    folded = _PUNCT.sub(" ", folded)
    cleaned = _SPACE.sub(" ", folded).strip()
    return f"{CACHE_VERSION}:{cleaned}" if cleaned else ""


class GeoCacheRepo:
    def __init__(self, table: Table | None = None) -> None:
        self.table = table or get_table()
        self.hits = 0
        self.misses = 0

    def get(self, query: str) -> list[GeoCandidate] | None:
        """Return cached candidates, or None on a miss.

        An empty list is a real cached answer -- "the geocoder knows of no such
        place" -- and is distinct from None. Collapsing the two would re-query
        every unresolvable location on every run.
        """
        key = normalise(query)
        if not key:
            return None
        body = self.table.get_body(geo_pk(key), "CACHE")
        if body is None:
            self.misses += 1
            return None
        self.hits += 1
        return [GeoCandidate.model_validate(c) for c in body.get("candidates", [])]

    def put(self, query: str, candidates: list[GeoCandidate]) -> None:
        key = normalise(query)
        if not key:
            return
        self.table.put_model(
            geo_pk(key),
            "CACHE",
            {
                "query": key,
                "original": query,
                "candidates": [c.model_dump(mode="json") for c in candidates],
            },
        )

    def stats(self) -> dict[str, int]:
        return {"hits": self.hits, "misses": self.misses}
