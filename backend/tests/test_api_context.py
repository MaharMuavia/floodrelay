"""The /context surface.

One rule shapes every test here: a context source that is down must degrade the
panel, never the console. The coordinator's queue, map and decision dock are the
product; satellite imagery and national damage figures are furniture around it.
If NASA, NDMA, Open-Meteo and ReliefWeb were all unreachable at once, /context
must still answer 200 with four honest "unavailable" blocks.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from floodrelay.agent.tools import imagery_layers
from floodrelay.main import create_app
from floodrelay.services.pipeline import PipelineService, set_pipeline_service
from floodrelay.store.table import Table

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def client(table: Table) -> Iterator[TestClient]:
    imagery_layers.reset_cache()
    set_pipeline_service(PipelineService(use_model=False))
    with TestClient(create_app()) as c:
        yield c
    set_pipeline_service(None)
    imagery_layers.reset_cache()


@respx.mock
def test_context_answers_200_even_with_every_source_down(client: TestClient) -> None:
    """The most important test in this file."""
    respx.route().mock(side_effect=httpx.ConnectError("no route"))

    response = client.get("/context")

    assert response.status_code == 200
    body = response.json()
    for block in ("river", "ndma", "reliefweb", "imagery"):
        assert body[block]["available"] is False
        assert body[block]["error"]


@respx.mock
def test_context_reports_river_discharge_when_it_is_up(client: TestClient) -> None:
    respx.get("https://flood-api.open-meteo.com/v1/flood").mock(
        return_value=httpx.Response(
            200,
            json={
                "daily": {
                    "time": [f"2026-09-{d:02d}" for d in range(1, 15)],
                    "river_discharge": [800.0] * 7 + [834.6] + [830.0] * 6,
                }
            },
        )
    )
    respx.route().mock(side_effect=httpx.ConnectError("no route"))

    body = client.get("/context").json()

    assert body["river"]["available"] is True
    assert body["river"]["current_m3s"] == 834.6


@respx.mock
def test_imagery_manifest_is_served_for_the_map(client: TestClient) -> None:
    respx.get(
        "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/1.0.0/WMTSCapabilities.xml"
    ).mock(
        return_value=httpx.Response(
            200, text=(FIXTURES / "gibs_capabilities.xml").read_text(encoding="utf-8")
        )
    )
    # The route probes one real tile per layer to establish whether it actually
    # serves anything over this district, so the tiles have to be mocked too.
    respx.route(
        method="GET", host="gibs.earthdata.nasa.gov", path__regex=r".*\.(png|jpg)$"
    ).mock(return_value=httpx.Response(200, content=b"fake-tile-bytes"))

    response = client.get("/context/imagery")

    assert response.status_code == 200
    body = response.json()
    assert body["available"] is True
    assert body["attribution"]
    flood = next(layer for layer in body["layers"] if layer["group"] == "flood")
    assert flood["max_zoom"] == 9
    assert flood["tile_url"].endswith("/{z}/{y}/{x}.png")


@respx.mock
def test_every_layer_carries_the_caveat_meant_to_reach_the_screen(
    client: TestClient,
) -> None:
    """A flood layer shown without its caveat and key reads backwards: grey is
    "Insufficient Data" (cloud), while clear is "No Water"."""
    respx.get(
        "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/1.0.0/WMTSCapabilities.xml"
    ).mock(
        return_value=httpx.Response(
            200, text=(FIXTURES / "gibs_capabilities.xml").read_text(encoding="utf-8")
        )
    )
    # The route probes one real tile per layer to establish whether it actually
    # serves anything over this district, so the tiles have to be mocked too.
    respx.route(
        method="GET", host="gibs.earthdata.nasa.gov", path__regex=r".*\.(png|jpg)$"
    ).mock(return_value=httpx.Response(200, content=b"fake-tile-bytes"))

    body = client.get("/context/imagery").json()

    assert body["layers"]
    for layer in body["layers"]:
        assert layer["caveat"].strip()


@respx.mock
def test_context_names_the_district_it_is_reporting_on(client: TestClient) -> None:
    respx.route().mock(side_effect=httpx.ConnectError("no route"))

    body = client.get("/context").json()

    assert body["district"] == "Nowshera"
    assert body["province"] == "KP"


@respx.mock
def test_coordinates_may_be_supplied_for_the_river_reading(client: TestClient) -> None:
    route = respx.get("https://flood-api.open-meteo.com/v1/flood").mock(
        return_value=httpx.Response(200, json={"daily": {}})
    )
    respx.route().mock(side_effect=httpx.ConnectError("no route"))

    client.get("/context?lat=33.99&lon=72.04")

    assert route.called
    request = route.calls[0].request
    assert "33.99" in str(request.url)


def test_out_of_range_coordinates_are_refused_at_the_boundary(
    client: TestClient,
) -> None:
    assert client.get("/context?lat=99&lon=0").status_code == 422
