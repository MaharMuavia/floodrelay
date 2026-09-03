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

from typing import Any

from ...config import get_settings
from ...models.common import Confidence, GeoCandidate, GeoPoint
from ..tool_agent import ToolCall
from ..tools.geocode import parse_coordinates, resolve

# A second candidate this close to the first is the same place under two names,
# not a genuine ambiguity.
SAME_PLACE_M = 2000.0

# Used only on the tool-calling path. It says nothing about confidence or
# thresholds: those are arithmetic and stay in Python, where a coordinator can
# read them.
_GEOLOCATE_PROMPT = """You place flood help requests on a map for a coordinator
working in one district of Khyber Pakhtunkhwa, Pakistan.

You have two tools:

- `geocode_place(query)` — resolve a village, landmark or neighbourhood name.
- `find_places(lat, lon, kinds)` — what is near a point, when a message names a
  landmark such as a school or mosque rather than a settlement.

## How to work

1. Read the message for the most specific place name it contains.
2. Call `geocode_place` with that name alone — no description, no punctuation.
3. If it returns nothing, try the next most specific name in the message. Stop
   after three attempts.
4. Never invent a place name that is not in the message, and never answer with
   coordinates you were not given by a tool.

Finish by naming the place you settled on in one short sentence. The coordinator
is shown the tool's own answer, not your sentence, so do not describe how
confident you are."""


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


def _point_from(
    candidates: list[GeoCandidate],
    query: str,
    *,
    cached: bool,
    attempt: int,
) -> tuple[GeoPoint | None, str]:
    """Turn a candidate list into a point with a stated confidence.

    Both paths below end here -- the one where Python called the geocoder and
    the one where the model did. The arithmetic that produces the confidence is
    therefore the same either way, which is the point: letting the model choose
    *which* place to look up is useful, letting it choose how confident we are
    about the answer is not.
    """
    picked = _confidence_from_candidates(candidates, query)
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
    suffix = " from cache" if cached else ""
    return point, f"resolved {query!r}{suffix} to {candidate.label}"


def _candidates_from_tool(payload: Any) -> list[GeoCandidate] | None:
    """Read a `geocode_place` result back into typed candidates.

    Returns None when the tool reported that it could not reach the geocoder,
    which is a different thing from "this place does not exist" and must not be
    collapsed into an empty list.
    """
    if not isinstance(payload, dict) or not payload.get("available", False):
        return None

    candidates: list[GeoCandidate] = []
    for row in payload.get("candidates") or []:
        if not isinstance(row, dict):
            continue
        try:
            candidates.append(
                GeoCandidate(
                    label=str(row["label"]),
                    lat=float(row["lat"]),
                    lon=float(row["lon"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return candidates


def run_with_agent(
    raw_text: str,
    location_text: str | None,
    *,
    attempt: int = 1,
    request_id: str | None = None,
    trace_id: str | None = None,
) -> tuple[GeoPoint | None, str, list[ToolCall]]:
    """Let the model decide what to look up, then score what it found.

    The model is given the whole message, not just the extracted location
    string, because the string is exactly the thing that has already failed when
    this runs on a retry. It may call `geocode_place` more than once and it may
    fall back to `find_places` for a landmark. Whatever it calls, the answer this
    node returns is built from the tool's own output -- never from the model's
    description of it.

    Returns (point, summary, calls). An empty `calls` means the model chose no
    tool at all, and the caller falls back to resolving in Python.
    """
    from ..tool_agent import run_agent
    from ..tools.geocode import geocode_place
    from ..tools.places import find_places

    known = (
        f"The extracted location string was {location_text!r}."
        if location_text
        else "No location string was extracted from this message."
    )
    retry_note = (
        " A previous attempt to place this request already failed, so look for a "
        "different name in the message rather than repeating the same query."
        if attempt > 1
        else ""
    )

    _text, trace = run_agent(
        role="light",
        tools=[geocode_place, find_places],
        system_prompt=_GEOLOCATE_PROMPT,
        user=(
            f"Message: {raw_text}\n{known}{retry_note}\n\n"
            f"Find where this request is, then say the place name you settled on."
        ),
        request_id=request_id,
        trace_id=trace_id,
        node="geolocate",
    )

    if not trace.calls:
        return None, "", []

    # Take the last geocode that actually returned somewhere. The model may probe
    # two or three names; the one it stopped on is the one it meant.
    for call in reversed(trace.calls):
        if call.name != "geocode_place" or not call.ok:
            continue
        candidates = _candidates_from_tool(call.output)
        if candidates is None:
            continue
        query = str(call.input.get("query") or location_text or "").strip()
        if not query:
            continue
        cached = bool(isinstance(call.output, dict) and call.output.get("cached"))
        point, summary = _point_from(candidates, query, cached=cached, attempt=attempt)
        if point is not None:
            return point, f"{summary} (the model chose this query)", trace.calls

    return None, "the model called the geocoder and it placed nothing", trace.calls


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

    return _point_from(result.candidates, query, cached=result.cached, attempt=attempt)


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
