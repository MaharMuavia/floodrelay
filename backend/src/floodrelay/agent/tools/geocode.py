"""Geocoding via Nominatim, cached forever and rate limited to 1 req/s.

Nominatim is free and run on donated infrastructure. The usage policy asks for
at most one request per second, a descriptive User-Agent with a contact address,
and that results be cached rather than re-fetched. All three are enforced here
in code. The cache is also what makes the demo replay safe: forty seed requests
resolve from the store without a single outbound call.

A location string that resolves to nothing is itself cached. "The geocoder knows
of no such place" is a real answer and re-asking will not change it.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from pydantic import Field

from ...config import get_settings
from ...models.common import GeoCandidate, Strict
from ...store.geocache_repo import GeoCacheRepo
from ._http import get_json, nominatim_limiter

# Plausible bounds for this deployment. A geocoder that confidently returns a
# point in the Atlantic for "Nowshera" should be treated as having failed, not
# as having found something.
PAKISTAN_BBOX = (23.5, 60.8, 37.1, 77.9)  # south, west, north, east

_COORD_RE = re.compile(
    r"(?<![\d.])(-?\d{1,2}\.\d{3,})\s*[,;\s]\s*(-?\d{1,3}\.\d{3,})(?![\d.])"
)


class GeocodeResult(Strict):
    """A typed outcome. Failures are values here, never exceptions.

    The brief specifies `geocode(query) -> list[GeoCandidate]`; wrapping that in
    a result object is what lets a failure carry its reason into the agent's
    context instead of unwinding the graph run.
    """

    query: str
    candidates: list[GeoCandidate] = Field(default_factory=list)
    cached: bool = False
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def parse_coordinates(text: str) -> GeoCandidate | None:
    """Pull an explicit lat/lon out of a message.

    Always preferred over geocoding a place name: a caller who sent coordinates
    has told us exactly where they are, and no amount of fuzzy matching improves
    on that.
    """
    match = _COORD_RE.search(text or "")
    if not match:
        return None
    lat, lon = float(match.group(1)), float(match.group(2))
    if not in_bounds(lat, lon):
        return None
    return GeoCandidate(lat=lat, lon=lon, label=f"{lat:.4f}, {lon:.4f}", kind="coordinates")


def in_bounds(
    lat: float, lon: float, bbox: tuple[float, float, float, float] = PAKISTAN_BBOX
) -> bool:
    south, west, north, east = bbox
    return south <= lat <= north and west <= lon <= east


def viewbox_bounds() -> tuple[float, float, float, float]:
    """The configured district box as (south, west, north, east)."""
    west, north, east, south = (float(v) for v in get_settings().geocode_viewbox.split(","))
    return south, west, north, east


def in_district(lat: float, lon: float) -> bool:
    """Whether a point falls inside the district being coordinated."""
    return in_bounds(lat, lon, viewbox_bounds())


def _fetch_nominatim(query: str, limit: int) -> tuple[list[GeoCandidate], str | None]:
    """The only place an outbound geocoding request is made."""
    s = get_settings()
    result = get_json(
        f"{s.nominatim_base}/search",
        params={
            "q": query,
            "format": "jsonv2",
            "limit": limit,
            "countrycodes": "pk",
            "addressdetails": 0,
            # Prefer results inside the district without excluding others.
            "viewbox": s.geocode_viewbox,
            "bounded": 0,
        },
        timeout=10.0,
        limiter=nominatim_limiter,
        # The console is English-only. Without this Nominatim returns local
        # names in Urdu script, which a coordinator reading an English queue
        # cannot scan.
        headers={"Accept-Language": "en"},
    )
    if not result.ok:
        if result.status == 403:
            return [], (
                f"{result.error} -- Nominatim rejected the User-Agent "
                f"{s.nominatim_user_agent!r}. It blocks placeholder contacts such as "
                f"example.org; set NOMINATIM_USER_AGENT to a real contact you control."
            )
        return [], result.error

    rows = result.data if isinstance(result.data, list) else []
    candidates: list[GeoCandidate] = []
    for row in rows:
        try:
            lat, lon = float(row["lat"]), float(row["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        if not in_bounds(lat, lon):
            continue
        candidates.append(
            GeoCandidate(
                lat=lat,
                lon=lon,
                label=str(row.get("display_name") or query),
                kind=str(row.get("type") or row.get("category") or "") or None,
                importance=float(row["importance"]) if row.get("importance") else None,
            )
        )

    # Put in-district matches first. Several Khyber Pakhtunkhwa villages share a
    # name across districts -- there is a Mohib Banda in Nowshera and another in
    # Mardan -- and Nominatim ranks by prominence, not by where we are working.
    candidates.sort(key=lambda c: (not in_district(c.lat, c.lon), -(c.importance or 0.0)))
    return candidates, None


def resolve(
    query: str,
    *,
    cache: GeoCacheRepo | None = None,
    fetch: Callable[[str, int], tuple[list[GeoCandidate], str | None]] | None = None,
    limit: int = 5,
) -> GeocodeResult:
    """Resolve a place string, consulting the permanent cache first.

    `fetch` is injectable so the cache test can prove that a repeated query
    makes no outbound call at all.
    """
    cleaned = (query or "").strip()
    if not cleaned:
        return GeocodeResult(query=query or "", error="empty query")

    repo = cache or GeoCacheRepo()
    hit = repo.get(cleaned)
    if hit is not None:
        return GeocodeResult(query=cleaned, candidates=hit, cached=True)

    candidates, error = (fetch or _fetch_nominatim)(cleaned, limit)
    if error is not None:
        # Do not cache a transport failure: the place may well resolve later.
        return GeocodeResult(query=cleaned, error=error)

    repo.put(cleaned, candidates)
    return GeocodeResult(query=cleaned, candidates=candidates, cached=False)


def warm_cache(queries: list[str]) -> dict[str, int]:
    """Pre-resolve the seed locations so a demo replay never waits on the network."""
    stats = {"resolved": 0, "cached": 0, "failed": 0}
    for q in queries:
        result = resolve(q)
        if not result.ok:
            stats["failed"] += 1
        elif result.cached:
            stats["cached"] += 1
        else:
            stats["resolved"] += 1
    return stats
