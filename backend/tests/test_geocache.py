"""The geocode cache.

Nominatim runs on donated infrastructure and asks for one request per second and
for results to be cached. The headline test here is that asking the same
question twice costs exactly one outbound call -- everything else in this file
guards the ways a cache quietly stops working.
"""

from __future__ import annotations

import pytest

from floodrelay.agent.tools.geocode import parse_coordinates, resolve
from floodrelay.models.common import GeoCandidate
from floodrelay.store.geocache_repo import CACHE_VERSION, GeoCacheRepo, normalise


class CountingFetcher:
    """Stands in for Nominatim and counts how often it was actually called."""

    def __init__(self, candidates: list[GeoCandidate] | None = None, error: str | None = None):
        self.calls = 0
        self.queries: list[str] = []
        self._candidates = candidates if candidates is not None else [
            GeoCandidate(lat=34.0151, lon=71.9747, label="Nowshera, Pakistan")
        ]
        self._error = error

    def __call__(self, query: str, limit: int) -> tuple[list[GeoCandidate], str | None]:
        self.calls += 1
        self.queries.append(query)
        return ([], self._error) if self._error else (list(self._candidates), None)


# --- the headline guarantee ------------------------------------------------


def test_a_repeated_query_makes_zero_network_calls(geocache: GeoCacheRepo) -> None:
    fetcher = CountingFetcher()

    first = resolve("Nowshera Kalan", cache=geocache, fetch=fetcher)
    assert first.ok and not first.cached
    assert fetcher.calls == 1

    second = resolve("Nowshera Kalan", cache=geocache, fetch=fetcher)
    assert second.ok and second.cached
    assert fetcher.calls == 1, "the second identical query must not touch the network"
    assert [c.model_dump() for c in second.candidates] == [
        c.model_dump() for c in first.candidates
    ]


@pytest.mark.parametrize(
    "variant",
    [
        "nowshera kalan",
        "  Nowshera   Kalan  ",
        "Nowshera, Kalan",
        "NOWSHERA KALAN.",
    ],
)
def test_cosmetic_variations_hit_the_same_cache_entry(
    geocache: GeoCacheRepo, variant: str
) -> None:
    """Case, spacing and punctuation must not silently defeat the cache."""
    fetcher = CountingFetcher()
    resolve("Nowshera Kalan", cache=geocache, fetch=fetcher)
    result = resolve(variant, cache=geocache, fetch=fetcher)
    assert result.cached
    assert fetcher.calls == 1


def test_different_places_are_cached_separately(geocache: GeoCacheRepo) -> None:
    fetcher = CountingFetcher()
    resolve("Nowshera Kalan", cache=geocache, fetch=fetcher)
    resolve("Pir Sabaq", cache=geocache, fetch=fetcher)
    assert fetcher.calls == 2


# --- caching the absence of an answer --------------------------------------


def test_an_empty_result_is_cached_too(geocache: GeoCacheRepo) -> None:
    """"No such place" is a real answer; re-asking will not change it."""
    fetcher = CountingFetcher(candidates=[])
    first = resolve("near the old bridge", cache=geocache, fetch=fetcher)
    second = resolve("near the old bridge", cache=geocache, fetch=fetcher)

    assert first.ok and first.candidates == []
    assert second.cached and second.candidates == []
    assert fetcher.calls == 1


def test_a_transport_failure_is_not_cached(geocache: GeoCacheRepo) -> None:
    """A timeout says nothing about the place, so it must be retried."""
    failing = CountingFetcher(error="timed out after 10.0s")
    first = resolve("Kheshgi Payan", cache=geocache, fetch=failing)
    assert not first.ok
    assert first.error == "timed out after 10.0s"

    ok = CountingFetcher()
    second = resolve("Kheshgi Payan", cache=geocache, fetch=ok)
    assert second.ok and not second.cached
    assert ok.calls == 1, "the failed lookup must not have poisoned the cache"


def test_an_empty_query_never_reaches_the_network(geocache: GeoCacheRepo) -> None:
    fetcher = CountingFetcher()
    for blank in ("", "   ", None):
        result = resolve(blank, cache=geocache, fetch=fetcher)  # type: ignore[arg-type]
        assert not result.ok
    assert fetcher.calls == 0


# --- key normalisation -----------------------------------------------------


def test_normalise_folds_case_punctuation_and_accents() -> None:
    assert normalise("  Nowshera,  Kalan! ") == f"{CACHE_VERSION}:nowshera kalan"
    assert normalise("NOWSHERA") == normalise("nowshera")


def test_the_cache_key_is_versioned() -> None:
    """The cache never evicts, so a changed request shape must not read stale answers."""
    assert normalise("Nowshera").startswith(f"{CACHE_VERSION}:")


def test_normalise_of_blank_is_empty() -> None:
    assert normalise("   ") == ""


# --- coordinates in the message -------------------------------------------


def test_explicit_coordinates_are_parsed_without_geocoding() -> None:
    got = parse_coordinates("34.0151, 71.9747 - stranded, water rising, 2 adults")
    assert got is not None
    assert got.lat == pytest.approx(34.0151)
    assert got.lon == pytest.approx(71.9747)


def test_space_separated_coordinates_are_parsed() -> None:
    got = parse_coordinates("34.0389 71.9012 two children fever high need doctor")
    assert got is not None
    assert got.lat == pytest.approx(34.0389)


def test_coordinates_outside_plausible_bounds_are_rejected() -> None:
    """A point in the Atlantic is a parsing accident, not a location."""
    assert parse_coordinates("0.0000, 0.0000 help") is None
    assert parse_coordinates("51.5074, -0.1278 London") is None


def test_prose_without_coordinates_yields_none() -> None:
    assert parse_coordinates("Mohib Banda near the boys school") is None
    assert parse_coordinates("") is None


# --- district disambiguation -------------------------------------------------
#
# Several Khyber Pakhtunkhwa villages share a name across districts: there is a
# Mohib Banda in Nowshera and another in Mardan. Nominatim ranks by prominence,
# not by where the coordinator is working, so a confident match can be 40 km
# outside the response area. That is worse than an ambiguous one, because
# nothing prompts anyone to question it.

from floodrelay.agent.nodes.geolocate import run as geolocate_run  # noqa: E402
from floodrelay.agent.tools.geocode import in_district  # noqa: E402

NOWSHERA = (34.0151, 71.9747)
MARDAN = (34.1988, 72.0404)  # a different district, ~40 km north


def test_in_district_accepts_the_response_area() -> None:
    assert in_district(*NOWSHERA)


def test_in_district_rejects_a_neighbouring_district() -> None:
    assert not in_district(*MARDAN)


def test_an_out_of_district_match_drops_below_the_gate_floor(
    geocache: GeoCacheRepo,
) -> None:
    """It must reach a human rather than being dispatched to the wrong village."""
    fetcher = CountingFetcher(
        candidates=[GeoCandidate(lat=MARDAN[0], lon=MARDAN[1], label="Mohib Banda, Mardan")]
    )
    resolve("Mohib Banda", cache=geocache, fetch=fetcher)

    point, _summary = geolocate_run("Mohib Banda near the school", "Mohib Banda")
    assert point is not None
    assert point.confidence.score < 0.55, "an out-of-district match must not pass the gate"
    assert "outside the district" in point.confidence.reason


def test_an_in_district_match_keeps_its_confidence(geocache: GeoCacheRepo) -> None:
    fetcher = CountingFetcher(
        candidates=[
            GeoCandidate(lat=NOWSHERA[0], lon=NOWSHERA[1], label="Pir Sabak, Nowshera District")
        ]
    )
    resolve("Pir Sabaq", cache=geocache, fetch=fetcher)

    point, _summary = geolocate_run("PIR SABAQ water at chest height", "Pir Sabaq")
    assert point is not None
    assert point.confidence.score >= 0.55


def test_coordinates_in_the_message_beat_a_geocoded_name() -> None:
    """The caller's own coordinates are never overridden by a place lookup."""
    point, summary = geolocate_run("34.0151, 71.9747 - stranded", "Mohib Banda")
    assert point is not None
    assert point.source == "coordinates_in_message"
    assert point.confidence.score == 0.95
    assert "coordinates from the message" in summary
