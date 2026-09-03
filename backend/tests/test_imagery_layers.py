"""Satellite layer manifest from NASA GIBS.

These tests exist because the failure mode this module guards against is silent:
if the manifest hands the map a date GIBS has not published yet, MapLibre asks
for tiles that do not exist and the layer simply does not draw. Nothing errors,
nothing logs, and the coordinator concludes there is no flood.

So the parser is tested against a faithful excerpt of the real capabilities
document -- single-quoted attributes, real namespaces, real Dimension shape --
rather than a tidied-up idealisation that would not catch that.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from floodrelay.agent.tools import imagery_layers
from floodrelay.store.table import Table

# Tests above the coverage section pass `probe=False`: they exercise parsing and
# caching, and a live tile probe is neither what they are asserting nor
# something they should have to mock.

FIXTURE = Path(__file__).parent / "fixtures" / "gibs_capabilities.xml"
CAPABILITIES_URL = (
    "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/1.0.0/WMTSCapabilities.xml"
)


@pytest.fixture
def capabilities_xml() -> str:
    return FIXTURE.read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _clear_memo() -> None:
    imagery_layers.reset_cache()


def by_id(result: dict[str, object], layer_id: str) -> dict[str, object]:
    layers = result["layers"]
    assert isinstance(layers, list)
    match = [layer for layer in layers if layer["id"] == layer_id]
    assert match, f"{layer_id} missing from {[layer['id'] for layer in layers]}"
    return match[0]  # type: ignore[no-any-return]


@respx.mock
def test_latest_date_comes_from_the_dimension_default(
    table: Table, capabilities_xml: str
) -> None:
    """GIBS states the newest published date; we must not infer our own."""
    respx.get(CAPABILITIES_URL).mock(
        return_value=httpx.Response(200, text=capabilities_xml)
    )

    result = imagery_layers.available_layers(table=table, probe=False)

    assert result["available"] is True
    assert by_id(result, "MODIS_Combined_Flood_1-Day")["latest"] == "2026-09-03"


@respx.mock
def test_half_hourly_layer_keeps_its_full_timestamp(
    table: Table, capabilities_xml: str
) -> None:
    """IMERG is published every 30 minutes; truncating to a date loses the tile."""
    respx.get(CAPABILITIES_URL).mock(
        return_value=httpx.Response(200, text=capabilities_xml)
    )

    result = imagery_layers.available_layers(table=table, probe=False)

    assert by_id(result, "IMERG_Precipitation_Rate_30min")["latest"] == (
        "2026-09-03T00:30:00Z"
    )


@respx.mock
def test_max_zoom_matches_the_tile_matrix_set_level(
    table: Table, capabilities_xml: str
) -> None:
    """The console opens at zoom 10. A Level9 layer served without a matching
    max_zoom vanishes exactly when the coordinator zooms in to look at it."""
    respx.get(CAPABILITIES_URL).mock(
        return_value=httpx.Response(200, text=capabilities_xml)
    )

    result = imagery_layers.available_layers(table=table, probe=False)

    assert by_id(result, "MODIS_Combined_Flood_1-Day")["max_zoom"] == 9
    assert by_id(result, "IMERG_Precipitation_Rate_30min")["max_zoom"] == 6
    assert by_id(result, "OPERA_L3_Dynamic_Surface_Water_Extent-Sentinel-1")[
        "max_zoom"
    ] == 12


@respx.mock
def test_tile_url_is_maplibre_ready_with_the_date_resolved(
    table: Table, capabilities_xml: str
) -> None:
    """The frontend should substitute nothing but z/y/x."""
    respx.get(CAPABILITIES_URL).mock(
        return_value=httpx.Response(200, text=capabilities_xml)
    )

    result = imagery_layers.available_layers(table=table, probe=False)
    url = by_id(result, "MODIS_Combined_Flood_1-Day")["tile_url"]
    assert isinstance(url, str)

    assert "2026-09-03" in url
    assert "GoogleMapsCompatible_Level9" in url
    assert url.endswith("/{z}/{y}/{x}.png")
    # WMTS placeholders must be gone, or MapLibre will request them literally.
    for placeholder in ("{Time}", "{TileMatrix}", "{TileRow}", "{TileCol}"):
        assert placeholder not in url


@respx.mock
def test_jpeg_layer_keeps_its_own_extension(table: Table, capabilities_xml: str) -> None:
    """True colour is JPEG, not PNG; a hardcoded .png would 404 every tile."""
    respx.get(CAPABILITIES_URL).mock(
        return_value=httpx.Response(200, text=capabilities_xml)
    )

    result = imagery_layers.available_layers(table=table, probe=False)
    url = by_id(result, "MODIS_Terra_CorrectedReflectance_TrueColor")["tile_url"]
    assert isinstance(url, str)

    assert url.endswith(".jpg")


@respx.mock
def test_curated_layer_absent_from_capabilities_is_omitted_not_invented(
    table: Table, capabilities_xml: str
) -> None:
    """The VIIRS layers are not in the fixture. They must not appear with a
    guessed date -- a plausible wrong date is worse than a missing layer."""
    respx.get(CAPABILITIES_URL).mock(
        return_value=httpx.Response(200, text=capabilities_xml)
    )

    result = imagery_layers.available_layers(table=table, probe=False)

    ids = [layer["id"] for layer in result["layers"]]  # type: ignore[union-attr]
    assert "VIIRS_Combined_Flood_1-Day" not in ids
    assert "VIIRS_Combined_Flood_1-Day" in result["unavailable"]


@respx.mock
def test_unreachable_service_returns_a_value_not_an_exception(table: Table) -> None:
    """Every tool in this layer reports failure as data. None of them raise."""
    respx.get(CAPABILITIES_URL).mock(side_effect=httpx.ConnectError("no route"))

    result = imagery_layers.available_layers(table=table, probe=False)

    assert result["available"] is False
    assert result["layers"] == []
    assert result.get("error")


@respx.mock
def test_malformed_xml_is_reported_rather_than_crashing(table: Table) -> None:
    respx.get(CAPABILITIES_URL).mock(
        return_value=httpx.Response(200, text="<Capabilities>truncated")
    )

    result = imagery_layers.available_layers(table=table, probe=False)

    assert result["available"] is False
    assert result.get("error")


@respx.mock
def test_second_call_is_served_from_cache_without_refetching(
    table: Table, capabilities_xml: str
) -> None:
    """The capabilities document is 5.8 MB. Fetching it per request would be
    indefensible on a metered link, and rude to GIBS."""
    route = respx.get(CAPABILITIES_URL).mock(
        return_value=httpx.Response(200, text=capabilities_xml)
    )

    first = imagery_layers.available_layers(table=table, probe=False)
    second = imagery_layers.available_layers(table=table, probe=False)

    assert route.call_count == 1
    assert first["layers"] == second["layers"]
    assert second["cached"] is True


@respx.mock
def test_cache_survives_a_new_process(table: Table, capabilities_xml: str) -> None:
    """The in-process memo is not enough: a restarted API must not re-download."""
    route = respx.get(CAPABILITIES_URL).mock(
        return_value=httpx.Response(200, text=capabilities_xml)
    )

    imagery_layers.available_layers(table=table, probe=False)
    imagery_layers.reset_cache()  # simulate a fresh process, same store
    second = imagery_layers.available_layers(table=table, probe=False)

    assert route.call_count == 1
    assert second["available"] is True


@respx.mock
def test_expired_cache_is_refetched(table: Table, capabilities_xml: str) -> None:
    route = respx.get(CAPABILITIES_URL).mock(
        return_value=httpx.Response(200, text=capabilities_xml)
    )

    imagery_layers.available_layers(table=table, probe=False)
    imagery_layers.reset_cache()
    imagery_layers.available_layers(table=table, max_age_s=0, probe=False)

    assert route.call_count == 2


@respx.mock
def test_stale_cache_is_served_when_the_refetch_fails(
    table: Table, capabilities_xml: str
) -> None:
    """A dated manifest beats no manifest, provided the age is visible."""
    respx.get(CAPABILITIES_URL).mock(
        return_value=httpx.Response(200, text=capabilities_xml)
    )
    imagery_layers.available_layers(table=table, probe=False)

    imagery_layers.reset_cache()
    respx.get(CAPABILITIES_URL).mock(side_effect=httpx.ConnectError("no route"))
    result = imagery_layers.available_layers(table=table, max_age_s=0, probe=False)

    assert result["available"] is True
    assert result["stale"] is True
    assert by_id(result, "MODIS_Combined_Flood_1-Day")["latest"] == "2026-09-03"


@respx.mock
def test_flood_layers_carry_the_published_colour_legend(
    table: Table, capabilities_xml: str
) -> None:
    """Without a key, the flood layer is not just unreadable -- it is actively
    misleading.

    Measured over Nowshera, 54-64% of a MODIS flood tile is opaque grey and only
    0.1-0.3% is any water class. GIBS's own colormap says grey is "Insufficient
    Data" (cloud) and fully transparent is "No Water". Read without the key, the
    grey wash looks like coverage and the transparent gaps look like nothing
    happening -- the exact inverse of the truth. These values come from
    gibs.earthdata.nasa.gov/colormaps/v1.3/MODIS_Flood.xml.
    """
    respx.get(CAPABILITIES_URL).mock(
        return_value=httpx.Response(200, text=capabilities_xml)
    )

    result = imagery_layers.available_layers(table=table, probe=False)
    flood = by_id(result, "MODIS_Combined_Flood_1-Day")
    legend = flood["legend"]
    assert isinstance(legend, list)

    labels = {entry["label"] for entry in legend}
    assert {"Surface Water", "Flood", "Insufficient Data"} <= labels

    insufficient = next(e for e in legend if e["label"] == "Insufficient Data")
    assert insufficient["rgb"] == "175,175,175"


@respx.mock
def test_the_cloud_caveat_does_not_claim_blank_means_cloud(
    table: Table, capabilities_xml: str
) -> None:
    """Regression guard on wording that was wrong in the dangerous direction.

    An earlier version of this caveat read "a blank layer means cloud". Blank is
    "No Water"; cloud is grey. A coordinator acting on the wrong one reads the
    map inside out.
    """
    respx.get(CAPABILITIES_URL).mock(
        return_value=httpx.Response(200, text=capabilities_xml)
    )

    caveat = str(by_id(imagery_layers.available_layers(table=table, probe=False),
                       "MODIS_Combined_Flood_1-Day")["caveat"])

    assert "blank" not in caveat.lower()
    assert "grey" in caveat.lower()


# ---------------------------------------------------------------------------
# Coverage probing
# ---------------------------------------------------------------------------
#
# GIBS declaring a layer for today does not mean it serves tiles *here*.
# Measured over Nowshera on 2026-09-03, six of ten curated layers returned 404
# at the district centre -- all three VIIRS flood products, Aqua true colour and
# the Sentinel-1 radar -- while their capabilities entries all said 2026-09-03.
#
# A layer offered in the picker that draws nothing is indistinguishable from a
# layer that looked and found no water. That is the single failure this whole
# module exists to prevent, so coverage is established by asking for a tile.

TILE_HOST = "https://gibs.earthdata.nasa.gov"


def _mock_capabilities(xml: str) -> None:
    respx.get(CAPABILITIES_URL).mock(return_value=httpx.Response(200, text=xml))


@respx.mock
def test_a_layer_that_serves_tiles_here_is_marked_covered(
    table: Table, capabilities_xml: str
) -> None:
    _mock_capabilities(capabilities_xml)
    respx.route(method="GET", host="gibs.earthdata.nasa.gov", path__regex=r".*\.(png|jpg)$").mock(
        return_value=httpx.Response(200, content=b"\x89PNG fake")
    )

    result = imagery_layers.available_layers(table=table)

    assert by_id(result, "MODIS_Combined_Flood_1-Day")["covers_district"] is True


@respx.mock
def test_a_layer_that_404s_here_is_listed_but_flagged(
    table: Table, capabilities_xml: str
) -> None:
    """Flagged, not hidden. The coordinator should be able to see that the
    layer exists and that it has nothing for today, which is itself a fact
    about the day."""
    _mock_capabilities(capabilities_xml)
    respx.route(method="GET", host="gibs.earthdata.nasa.gov", path__regex=r".*\.(png|jpg)$").mock(
        return_value=httpx.Response(404)
    )

    result = imagery_layers.available_layers(table=table)

    layer = by_id(result, "MODIS_Combined_Flood_1-Day")
    assert layer["covers_district"] is False
    assert result["available"] is True


@respx.mock
def test_a_probe_that_cannot_run_says_unknown_rather_than_guessing(
    table: Table, capabilities_xml: str
) -> None:
    """A failed probe is not evidence of absence, and must not be recorded as
    though it were."""
    _mock_capabilities(capabilities_xml)
    respx.route(method="GET", host="gibs.earthdata.nasa.gov", path__regex=r".*\.(png|jpg)$").mock(
        side_effect=httpx.ConnectError("no route")
    )

    result = imagery_layers.available_layers(table=table)

    assert result["available"] is True
    assert by_id(result, "MODIS_Combined_Flood_1-Day")["covers_district"] is None


@respx.mock
def test_coverage_is_cached_with_the_manifest(
    table: Table, capabilities_xml: str
) -> None:
    """Probing is ten extra requests. Doing it per page load would be rude."""
    _mock_capabilities(capabilities_xml)
    probe = respx.route(
        method="GET", host="gibs.earthdata.nasa.gov", path__regex=r".*\.(png|jpg)$"
    ).mock(return_value=httpx.Response(200, content=b"\x89PNG fake"))

    imagery_layers.available_layers(table=table)
    before = probe.call_count
    imagery_layers.reset_cache()
    imagery_layers.available_layers(table=table)

    assert probe.call_count == before


@respx.mock
def test_probing_can_be_switched_off(table: Table, capabilities_xml: str) -> None:
    _mock_capabilities(capabilities_xml)
    probe = respx.route(
        method="GET", host="gibs.earthdata.nasa.gov", path__regex=r".*\.(png|jpg)$"
    ).mock(return_value=httpx.Response(200, content=b"\x89PNG fake"))

    result = imagery_layers.available_layers(table=table, probe=False)

    assert probe.call_count == 0
    assert by_id(result, "MODIS_Combined_Flood_1-Day")["covers_district"] is None
