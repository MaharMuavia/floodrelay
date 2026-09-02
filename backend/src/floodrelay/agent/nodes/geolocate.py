"""The geolocate node: a place string in, a `GeoPoint` with a stated confidence out.

Order of preference:

1. **Coordinates already in the message.** A caller who sent a lat/lon has told
   us exactly where they are. Nothing improves on that, so we never geocode over it.
2. **A cached or fresh Nominatim result** for the extracted location text.
3. **Nothing**, with a confidence low enough to route to the gate.

Confidence always carries a reason in plain language, because the coordinator
sees it on the card when a location is doubtful and "0.41" on its own is not
something a person can act on.
"""

from __future__ import annotations

from ...config import get_settings
from ...models.common import Confidence, GeoCandidate, GeoPoint
from ..tools.geocode import parse_coordinates, resolve

# A second candidate this close to the first is the same place under two names,
# not a genuine ambiguity.
SAME_PLACE_M = 2000.0


def _confidence_from_candidates(
    candidates: list[GeoCandidate], query: str
) -> tuple[GeoCandidate, Confidence] | None:
    if not candidates:
        return None

    from ...services.geo import haversine_m

    best = candidates[0]
    if len(candidates) == 1:
        return best, Confidence(
            score=0.82,
            reason=f"one match for {query!r}: {best.label}",
        )

    second = candidates[1]
    spread_m = haversine_m(best.lat, best.lon, second.lat, second.lon)
    if spread_m <= SAME_PLACE_M:
        return best, Confidence(
            score=0.78,
            reason=(
                f"{len(candidates)} matches for {query!r}, but the top two are "
                f"{spread_m / 1000:.1f} km apart, so they are the same place"
            ),
        )

    return best, Confidence(
        score=0.45,
        reason=(
            f"{len(candidates)} matches for {query!r} that are {spread_m / 1000:.0f} km "
            f"apart; picked {best.label}, but this needs confirming"
        ),
    )


def run(
    raw_text: str,
    location_text: str | None,
    *,
    attempt: int = 1,
) -> tuple[GeoPoint | None, str]:
    """Resolve a location. Returns the point (or None) and a short summary."""
    coords = parse_coordinates(raw_text)
    if coords is not None:
        point = GeoPoint(
            lat=coords.lat,
            lon=coords.lon,
            label=coords.label,
            source="coordinates_in_message",
            confidence=Confidence(
                score=0.95, reason="the message contained explicit coordinates"
            ),
        )
        return point, f"used coordinates from the message ({coords.label})"

    query = (location_text or "").strip()
    if not query:
        return None, "the message did not name a location"

    result = resolve(query)
    if not result.ok:
        return None, f"the geocoder could not be reached: {result.error}"

    picked = _confidence_from_candidates(result.candidates, query)
    if picked is None:
        return None, f"no match for {query!r}"

    candidate, confidence = picked

    # A confident match in the wrong district is worse than an ambiguous one:
    # it dispatches a boat to a village 40 km away with no one questioning it.
    from ..tools.geocode import in_district

    if not in_district(candidate.lat, candidate.lon):
        confidence = Confidence(
            score=0.35,
            reason=(
                f"{candidate.label} is outside the district being coordinated. "
                f"Several villages here share a name across districts, so this "
                f"needs confirming before anyone is sent."
            ),
        )

    if attempt > 1:
        # A second pass that still lands here is worth less than the first.
        confidence = Confidence(
            score=round(confidence.score * 0.9, 3),
            reason=f"{confidence.reason} (second attempt)",
        )

    point = GeoPoint(
        lat=candidate.lat,
        lon=candidate.lon,
        label=candidate.label,
        source="nominatim",
        confidence=confidence,
    )
    cached = " from cache" if result.cached else ""
    return point, f"resolved {query!r}{cached} to {candidate.label}"


def needs_retry(point: GeoPoint | None, attempt: int) -> bool:
    """Whether to loop back to extract for a better location string.

    Once only. A second failure is a decision for a human, not a third guess.
    """
    if attempt > 1:
        return False
    floor = get_settings().geo_confidence_floor
    return point is None or point.confidence.score < floor


def is_low_confidence(point: GeoPoint | None) -> bool:
    if point is None:
        return True
    return point.confidence.score < get_settings().geo_confidence_floor
