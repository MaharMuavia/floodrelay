"""Satellite tile layers from NASA GIBS.

GIBS serves near-real-time imagery as WMTS tiles with no key, no registration
and no account. The map can therefore draw real flood extent over the district
without this project acquiring a credential it would then have to ship.

What this module is for
-----------------------
The tiles need no backend to render. They need a backend to answer one
question: **which date should the map ask for?** GIBS publishes on its own
schedule, so "today" is frequently not available yet. Ask for a date GIBS has
not published and every tile 404s -- MapLibre draws nothing, logs nothing, and
the coordinator concludes there is no flood. That failure is silent and
dangerous, so the date is read from what GIBS declares rather than guessed.

Two limits are carried on every layer and are meant to reach the screen:

* **Optical sensors cannot see through cloud.** MODIS and VIIRS are optical.
  During a monsoon flood it is cloudy, which is exactly when the layer is
  wanted. Grey is "Insufficient Data" -- cloud -- and clear is "No Water";
  measured over Nowshera the grey covers 54-64% of a tile while every water
  class together covers under 0.3%. A coordinator who reads the grey as
  coverage and the clear gaps as calm has the picture exactly inverted, which
  is why the legend is carried to the screen rather than documented here.
* **Resolution is 250 m.** These products describe areas, never households.
  Nothing here may be used to confirm or deny an individual request -- that
  would invent precision the sensor does not have, the same error that caused
  photo severity scoring to be switched off.

Like every other tool in this package, nothing here influences an urgency
score and nothing here raises into a caller.
"""

from __future__ import annotations

import math
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any
from xml.etree import ElementTree

from ...config import get_settings
from ...store.table import Table, get_table, gibs_pk
from ._http import get_bytes, get_text

NS = {
    "wmts": "http://www.opengis.net/wmts/1.0",
    "ows": "http://www.opengis.net/ows/1.1",
}

# The capabilities document is ~5.8 MB and lists 3,914 layers. Six hours is
# comfortably finer than the daily publication cadence of the flood products
# and keeps the download to four a day.
MAX_AGE_S = 6 * 60 * 60

CACHE_SK = "CACHE"
CACHE_NAME = "capabilities"

# Measured over Nowshera: 54-64% of a MODIS flood tile is opaque grey and only
# 0.1-0.3% is any water class. Grey is "Insufficient Data" -- almost always
# cloud -- while fully transparent is "No Water". Read without that, the grey
# wash looks like coverage and the clear gaps look like nothing happening, which
# is the exact inverse of the truth.
CLOUD = (
    "Optical sensor. Grey is insufficient data -- usually cloud -- not dry "
    "ground. Clear is no water; blue, yellow and red are water and flood. "
    "250 m pixels describe areas, never households."
)
AREA_ONLY = "250 m pixels describe areas, not households."

# Published by GIBS at colormaps/v1.3/MODIS_Flood.xml. Carried through to the
# screen because the layer is unreadable, and misreadable, without it.
FLOOD_LEGEND: tuple[dict[str, str], ...] = (
    {"rgb": "50,210,245", "label": "Surface Water"},
    {"rgb": "255,255,0", "label": "Recurring Flood"},
    {"rgb": "250,30,36", "label": "Flood"},
    {"rgb": "175,175,175", "label": "Insufficient Data"},
)


@dataclass(frozen=True)
class LayerSpec:
    """One curated layer. `caveat` is written to be shown, not filed away."""

    id: str
    title: str
    group: str
    caveat: str
    legend: tuple[dict[str, str], ...] = ()


# Curated rather than exposing all 3,914: every entry here has been checked to
# return real tiles over the district being coordinated.
CURATED: tuple[LayerSpec, ...] = (
    LayerSpec(
        "MODIS_Combined_Flood_1-Day",
        "Flood extent, 1-day (MODIS)",
        "flood",
        CLOUD,
        FLOOD_LEGEND,
    ),
    LayerSpec(
        "MODIS_Combined_Flood_2-Day",
        "Flood extent, 2-day (MODIS)",
        "flood",
        CLOUD,
        FLOOD_LEGEND,
    ),
    LayerSpec(
        "MODIS_Combined_Flood_3-Day",
        "Flood extent, 3-day (MODIS)",
        "flood",
        CLOUD,
        FLOOD_LEGEND,
    ),
    LayerSpec(
        "VIIRS_Combined_Flood_1-Day",
        "Flood extent, 1-day (VIIRS)",
        "flood",
        CLOUD,
        FLOOD_LEGEND,
    ),
    LayerSpec(
        "VIIRS_Combined_Flood_2-Day",
        "Flood extent, 2-day (VIIRS)",
        "flood",
        CLOUD,
        FLOOD_LEGEND,
    ),
    LayerSpec(
        "VIIRS_Combined_Flood_3-Day",
        "Flood extent, 3-day (VIIRS)",
        "flood",
        CLOUD,
        FLOOD_LEGEND,
    ),
    LayerSpec(
        "MODIS_Terra_CorrectedReflectance_TrueColor",
        "True colour (Terra)",
        "imagery",
        CLOUD,
    ),
    LayerSpec(
        "MODIS_Aqua_CorrectedReflectance_TrueColor",
        "True colour (Aqua)",
        "imagery",
        CLOUD,
    ),
    LayerSpec(
        "IMERG_Precipitation_Rate_30min",
        "Rainfall rate, 30-minute (IMERG)",
        "rain",
        "Satellite estimate, half-hourly. Coarse resolution.",
    ),
    LayerSpec(
        "OPERA_L3_Dynamic_Surface_Water_Extent-Sentinel-1",
        "Surface water, radar (Sentinel-1)",
        "flood",
        "Radar sees through cloud, but coverage is scene-based: this layer is "
        "often absent over a given district on a given day.",
    ),
)

_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/jpg": "jpg"}
_LEVEL = re.compile(r"_Level(\d+)$")

# Process-wide memo so repeated /context calls do not even touch the store.
_memo: dict[str, Any] | None = None
_memo_at: float = 0.0


def reset_cache() -> None:
    """Drop the in-process memo. The stored copy is deliberately left alone."""
    global _memo, _memo_at
    _memo = None
    _memo_at = 0.0


def capabilities_url() -> str:
    return f"{get_settings().gibs_base}/1.0.0/WMTSCapabilities.xml"


def _max_zoom(tile_matrix_set: str) -> int | None:
    """Zoom ceiling implied by the TileMatrixSet name.

    Load-bearing. The console opens at zoom 10; a Level9 layer whose source
    omits `maxzoom` makes MapLibre request tiles that do not exist, so the layer
    disappears at exactly the moment someone zooms in to look at it.
    """
    found = _LEVEL.search(tile_matrix_set)
    return int(found.group(1)) if found else None


def _tile_url(template: str, latest: str, tile_matrix_set: str) -> str:
    """Turn the WMTS template GIBS publishes into one MapLibre can consume."""
    return (
        template.replace("{Time}", latest)
        .replace("{TileMatrixSet}", tile_matrix_set)
        .replace("{TileMatrix}", "{z}")
        .replace("{TileRow}", "{y}")
        .replace("{TileCol}", "{x}")
    )


def _latest_from(dimension: ElementTree.Element) -> str | None:
    """Prefer the Default GIBS states; fall back to the newest range end.

    GIBS names the newest published date itself. Deriving our own from the
    ranges would be guessing at the very thing we are trying not to guess at.
    """
    default = dimension.find("wmts:Default", NS)
    if default is not None and (default.text or "").strip():
        return (default.text or "").strip()

    values = [v.text.strip() for v in dimension.findall("wmts:Value", NS) if v.text]
    if not values:
        return None
    parts = values[-1].split("/")
    return parts[1] if len(parts) >= 2 else parts[0]


def _parse(xml: str) -> dict[str, Any]:
    """Extract the curated layers. Raises ElementTree.ParseError on bad XML."""
    root = ElementTree.fromstring(xml)
    wanted = {spec.id: spec for spec in CURATED}
    found: dict[str, dict[str, Any]] = {}

    for layer in root.iter(f"{{{NS['wmts']}}}Layer"):
        identifier = layer.find("ows:Identifier", NS)
        if identifier is None or (identifier.text or "").strip() not in wanted:
            continue
        layer_id = (identifier.text or "").strip()
        spec = wanted[layer_id]

        dimension = layer.find("wmts:Dimension", NS)
        tms_el = layer.find("wmts:TileMatrixSetLink/wmts:TileMatrixSet", NS)
        if dimension is None or tms_el is None or not (tms_el.text or "").strip():
            continue

        latest = _latest_from(dimension)
        tile_matrix_set = (tms_el.text or "").strip()
        if not latest:
            continue

        template = None
        for resource in layer.findall("wmts:ResourceURL", NS):
            if resource.get("resourceType") != "tile":
                continue
            candidate = resource.get("template") or ""
            if "{Time}" in candidate:
                template = candidate
                break
        if not template:
            continue

        fmt = layer.find("wmts:Format", NS)
        media_type = (fmt.text or "").strip() if fmt is not None else "image/png"

        found[layer_id] = {
            "id": layer_id,
            "title": spec.title,
            "group": spec.group,
            "caveat": spec.caveat,
            "legend": list(spec.legend),
            "latest": latest,
            "tile_matrix_set": tile_matrix_set,
            "max_zoom": _max_zoom(tile_matrix_set),
            "format": media_type,
            "tile_url": _tile_url(template, latest, tile_matrix_set),
            # Filled in by _probe_coverage; None means "not established".
            "covers_district": None,
        }

    # Order follows CURATED so the control reads the same way every time.
    layers = [found[spec.id] for spec in CURATED if spec.id in found]
    unavailable = [spec.id for spec in CURATED if spec.id not in found]
    return {
        "available": bool(layers),
        "layers": layers,
        "unavailable": unavailable,
        "attribution": "Imagery courtesy NASA EOSDIS GIBS",
        "source_url": capabilities_url(),
    }


def _tile_xyz(lat: float, lon: float, zoom: int) -> tuple[int, int, int]:
    """Slippy-map tile containing a point, in the usual Web Mercator scheme."""
    n = 2**zoom
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n)
    return zoom, min(max(y, 0), n - 1), min(max(x, 0), n - 1)


def _probe_layer(layer: dict[str, Any], lat: float, lon: float) -> bool | None:
    """Ask for one real tile over the district.

    Returns True if a tile came back, False on a 404 (the layer publishes
    nothing here today), and None if the probe itself could not run -- a failed
    probe is not evidence of absence and must not be recorded as though it were.
    """
    zoom = min(8, layer.get("max_zoom") or 8)
    z, y, x = _tile_xyz(lat, lon, zoom)
    url = (
        str(layer["tile_url"])
        .replace("{z}", str(z))
        .replace("{y}", str(y))
        .replace("{x}", str(x))
    )
    result = get_bytes(url, timeout=15.0, max_bytes=4 * 1024 * 1024)
    if result.ok:
        return True
    if result.status == 404:
        return False
    return None


def _probe_coverage(layers: list[dict[str, Any]], lat: float, lon: float) -> None:
    """Fill in `covers_district` for every layer, concurrently.

    Ten sequential probes would add ten seconds to a cold manifest. They are
    independent, small, and cached for six hours behind the manifest, so a
    small pool is both faster and still well inside anything GIBS would mind.
    """
    if not layers:
        return
    with ThreadPoolExecutor(max_workers=5) as pool:
        verdicts = list(pool.map(lambda item: _probe_layer(item, lat, lon), layers))
    for layer, verdict in zip(layers, verdicts, strict=True):
        layer["covers_district"] = verdict


def _memoise(payload: dict[str, Any], fetched_at: float) -> None:
    global _memo, _memo_at
    _memo = payload
    _memo_at = fetched_at


def available_layers(
    *, table: Table | None = None, max_age_s: float = MAX_AGE_S, probe: bool = True
) -> dict[str, Any]:
    """The satellite layer manifest: which layers exist and for which date.

    Never raises. On failure with nothing cached, returns `available: false`
    and a reason. On failure with something cached, returns the cached manifest
    marked `stale` -- a dated manifest beats no manifest, provided the age is
    visible on screen.
    """
    now = time.time()
    if _memo is not None and (now - _memo_at) < max_age_s:
        return {**_memo, "cached": True, "stale": False, "fetched_at": _memo_at}

    store = table or get_table()
    key = gibs_pk(CACHE_NAME)
    stored = store.get_body(key, CACHE_SK)
    stored_at = float(stored.get("fetched_at", 0.0)) if stored else 0.0
    stored_payload = stored.get("payload") if stored else None

    if stored_payload and (now - stored_at) < max_age_s:
        _memoise(stored_payload, stored_at)
        return {**stored_payload, "cached": True, "stale": False, "fetched_at": stored_at}

    result = get_text(capabilities_url(), timeout=60.0)
    error: str | None = None
    if result.ok:
        try:
            payload = _parse(str(result.data))
        except ElementTree.ParseError as exc:
            error = f"GIBS capabilities was not valid XML: {exc}"
        else:
            if probe:
                settings = get_settings()
                _probe_coverage(
                    payload["layers"], settings.situation_lat, settings.situation_lon
                )
            store.put_model(key, CACHE_SK, {"fetched_at": now, "payload": payload})
            _memoise(payload, now)
            return {**payload, "cached": False, "stale": False, "fetched_at": now}
    else:
        error = result.error

    if stored_payload:
        _memoise(stored_payload, stored_at)
        return {
            **stored_payload,
            "cached": True,
            "stale": True,
            "fetched_at": stored_at,
            "error": error,
        }

    return {
        "available": False,
        "layers": [],
        "unavailable": [spec.id for spec in CURATED],
        "error": error or "GIBS capabilities could not be read",
        "source_url": capabilities_url(),
        "cached": False,
        "stale": False,
    }
