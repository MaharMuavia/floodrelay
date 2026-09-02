"""Nearby shelters, hospitals and schools from OpenStreetMap via Overpass.

Cached by rounded coordinate tile: two requests from the same neighbourhood ask
Overpass once. Overpass is donated infrastructure like Nominatim, so the same
restraint applies.
"""

from __future__ import annotations

from typing import Any

from strands import tool

from ...config import get_settings
from ...models.common import GeoCandidate
from ...store.geocache_repo import GeoCacheRepo
from ._http import overpass_limiter, post_text

# Tile size for the cache key. ~1.1 km, which is the scale at which "what is
# near me" gives the same answer for everyone in a neighbourhood.
TILE_DEG = 0.01

KIND_QUERIES: dict[str, str] = {
    "shelter": '["amenity"~"shelter|community_centre"]',
    "hospital": '["amenity"~"hospital|clinic|doctors"]',
    "school": '["amenity"~"school|college|university"]',
    "mosque": '["amenity"="place_of_worship"]',
}


def _tile_key(lat: float, lon: float, kinds: list[str], radius_m: int) -> str:
    return (
        f"overpass {lat // TILE_DEG:.0f} {lon // TILE_DEG:.0f} "
        f"{'-'.join(sorted(kinds))} {radius_m}"
    )


def find(
    lat: float,
    lon: float,
    kinds: list[str] | None = None,
    radius_m: int = 3000,
    *,
    cache: GeoCacheRepo | None = None,
) -> dict[str, Any]:
    kinds = kinds or ["shelter", "hospital", "school"]
    known = [k for k in kinds if k in KIND_QUERIES]
    if not known:
        return {"available": False, "error": f"unknown kinds: {kinds}", "places": []}

    repo = cache or GeoCacheRepo()
    key = _tile_key(lat, lon, known, radius_m)
    hit = repo.get(key)
    if hit is not None:
        return {"available": True, "cached": True, "places": [c.model_dump() for c in hit]}

    clauses = "".join(
        f'node{KIND_QUERIES[k]}(around:{radius_m},{lat:.5f},{lon:.5f});'
        f'way{KIND_QUERIES[k]}(around:{radius_m},{lat:.5f},{lon:.5f});'
        for k in known
    )
    query = f"[out:json][timeout:20];({clauses});out center 40;"

    result = post_text(get_settings().overpass_base, content=query, limiter=overpass_limiter)
    if not result.ok:
        return {"available": False, "error": result.error, "places": []}

    candidates: list[GeoCandidate] = []
    for el in (result.data or {}).get("elements", []):
        plat = el.get("lat") or (el.get("center") or {}).get("lat")
        plon = el.get("lon") or (el.get("center") or {}).get("lon")
        if plat is None or plon is None:
            continue
        tags = el.get("tags", {}) or {}
        candidates.append(
            GeoCandidate(
                lat=float(plat),
                lon=float(plon),
                label=str(tags.get("name") or tags.get("amenity") or "unnamed"),
                kind=str(tags.get("amenity") or "") or None,
            )
        )

    repo.put(key, candidates)
    return {"available": True, "cached": False, "places": [c.model_dump() for c in candidates]}


@tool
def find_places(
    lat: float, lon: float, kinds: list[str] | None = None, radius_m: int = 3000
) -> dict[str, Any]:
    """Find shelters, hospitals, schools or mosques near a point, from OpenStreetMap.

    Useful for suggesting where people could be moved to when no roster shelter
    is close enough.

    Args:
        lat: Latitude.
        lon: Longitude.
        kinds: Any of shelter, hospital, school, mosque. Defaults to the first three.
        radius_m: Search radius in metres.

    Returns:
        A dict with `places` (name, lat, lon, kind). Returns `available: false`
        with an `error` rather than raising if Overpass cannot be reached.
    """
    return find(lat, lon, kinds, radius_m)
