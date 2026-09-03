# Real flood data: satellite layers and Pakistani official sources

Date: 2026-09-03
Status: implemented and verified against live sources on 2026-09-03

## Problem

Every help request in FloodRelay is synthetic, and correctly so. But the console
currently shows the coordinator nothing real about the flood those requests
describe. The map has pins on an OSM basemap and no water on it.

This spec adds a **situation layer** of real, live data around the synthetic
queue. It does not add real requests, and it never will: the README's promise
that "no real person, phone number, or address appears anywhere in this
repository" is one of the project's strongest claims and is not up for trade.

## What does not change

These are constraints, not preferences.

1. **`seed/requests.jsonl` stays synthetic.** PDMA and NDMA publish district
   aggregates -- deaths, houses damaged, camps established. Nothing in them is a
   household saying it is on a roof. Real data cannot replace the queue and must
   not try to.
2. **`services/scoring.py` is untouched.** Every source here is context, on the
   same contract `weather.py` already states: it informs the coordinator's
   explanation and contributes nothing to the deterministic urgency number.
   A test asserts this directly rather than trusting a comment.
3. **The human gate is untouched.** No source here can be an input to dispatch.
4. **Failures are named, never hidden.** Every block carries `available`,
   `source_url` and `as_of`. When something cannot be fetched or parsed, the
   console says which source and why, following the pattern `score_photo`
   already sets.

## Verified source inventory

Every row was probed on 2026-09-03. Results are recorded so a later reader can
tell what was actually checked from what was assumed.

### Satellite -- NASA GIBS (no key, no registration)

Base: `https://gibs.earthdata.nasa.gov/wmts/epsg3857/best`
Template: `/{layer}/default/{time}/{tileMatrixSet}/{z}/{y}/{x}.{ext}`

Capabilities document: HTTP 200, 5.8 MB, 3,914 layer identifiers.

| Layer | TileMatrixSet | Format | Latest | Probe over Nowshera |
|---|---|---|---|---|
| `MODIS_Combined_Flood_1-Day` | `GoogleMapsCompatible_Level9` | png | 2026-09-03 | 200, 6,264 B |
| `MODIS_Combined_Flood_2-Day` | `GoogleMapsCompatible_Level9` | png | 2026-09-03 | 200, 5,922 B |
| `MODIS_Combined_Flood_3-Day` | `GoogleMapsCompatible_Level9` | png | 2026-09-03 | listed |
| `VIIRS_Combined_Flood_1/2/3-Day` | `GoogleMapsCompatible_Level9` | png | 2026-09-03 | **404 — declared but not served here** |
| `MODIS_Terra_CorrectedReflectance_TrueColor` | `GoogleMapsCompatible_Level9` | jpeg | 2026-09-03 | 200, 8,400 B |
| `IMERG_Precipitation_Rate_30min` | `GoogleMapsCompatible_Level6` | png | 2026-09-03T00:30Z | 200, 2,597 B |
| `OPERA_L3_..._Extent-Sentinel-1` | `GoogleMapsCompatible_Level12` | png | 2026-08-26 | **404 at z8/z10, all dates tried** |

### Non-satellite

| Source | Key? | Probe |
|---|---|---|
| Open-Meteo Flood API (GloFAS v4 river discharge) | no | 200, 834 m3/s at Nowshera, 7d back + 7d forward |
| NDMA daily monsoon sitrep listing | no | 200, latest **No. 69, 02 Sep 2026** |
| NDMA sitrep PDF | no | 200, 3.1 MB, 18 pages, **text-based** (5 embedded fonts, 22 text streams) |
| ReliefWeb API v1 | -- | **410 Gone -- decommissioned** |
| ReliefWeb API v2 | approved appname required | 403 with unapproved appname |
| ReliefWeb RSS | no | 200, 20 items |
| GDACS RSS | no | 200, 378 items, 13 flood alerts, **0 Pakistan** |
| PMD Flood Forecasting Division | -- | unreachable (connection failure) |
| PDMA KP website | no | 200, plans and notifications only; no daily sitrep feed |

## Known limits, to be stated on screen

**Optical sensors cannot see through cloud.** MODIS and VIIRS flood products are
optical. During an active monsoon flood it is cloudy, which is exactly when the
data is wanted. The 2-Day and 3-Day composites exist to mitigate this and only
partly succeed. The layer control must say so.

*Corrected during implementation.* This section originally read "a blank flood
layer means cloud". That is backwards, and dangerously so. Fetching real tiles
and reading GIBS's published colormap
(`colormaps/v1.3/MODIS_Flood.xml`) gives the actual classes: fully transparent
is **No Water**, and opaque grey `175,175,175` is **Insufficient Data** — which
in practice means cloud. Measured over Nowshera, grey covers **54–64%** of a
tile while Surface Water, Recurring Flood and Flood together cover **under
0.3%**. A coordinator reading grey as coverage and the clear gaps as calm has
the picture exactly inverted. The layer therefore ships with the published
colour key on screen, not merely a sentence of prose.

**Sentinel-1 SAR sees through cloud but was not available here.** Probes over
Nowshera returned 404 at z8 and z10 across 2026-08-14, 08-20 and 08-26; only a
near-empty z6 tile on 08-26 returned 200. SAR is scene-based, not global-daily.
The layer ships best-effort and renders an explicit "no coverage for this date"
state, which is expected to be its usual state over this district.

**250 m pixels describe areas, not households.** The flood products must never
be used to confirm or deny an individual request. Doing so would invent
precision the sensor does not have -- the same error that caused photo severity
scoring to be switched off. This is a review rule, not just documentation.

**GDACS — deferred, then built.** At the time of the first probe the feed
carried 378 items, 13 flood alerts and nothing for Pakistan, so it was deferred
as adding a capability with nothing to show. On re-probing during
implementation it carried a Pakistan flood alert, and it was built. Three things
about it are worth recording:

- **Severity comes from `gdacs:alertlevel`, never the title.** The feed
  disagrees with itself: an item titled "Orange flood alert in Nepal" carries
  `<gdacs:alertlevel>Red</gdacs:alertlevel>`. Reading the title would
  under-report a red alert.
- **GDACS answers 406 to `Accept: text/xml`**, the shared default in `_http`.
  Every mocked test passed while the live feed returned nothing.
- **The feed is 1.1 MB and took 15.8s**, which made it 15.8 of `/context`'s
  21.4 seconds on every panel load in every tab. Cached for fifteen minutes;
  warm `/context` is now 3.4s.

## Phase 1 -- Satellite layers on the map

The tiles need no backend to render. They do need a backend to know *which date*
to ask for, because "today" is often not published yet and guessing produces
silently blank tiles.

**Backend: `agent/tools/imagery_layers.py`**

- `available_dates()` -- fetches the GIBS capabilities document, extracts the
  time extent for only the layers listed above, returns
  `{layer: {latest, tile_matrix_set, format, max_zoom}}`.
- The capabilities document is 5.8 MB. It is fetched **at most once every six
  hours** and the extracted result (a few hundred bytes) is cached in the store
  as `GIBS#capabilities`, following the `geocache_repo.py` pattern.
- On failure: `available: false` with the reason. The frontend then hides the
  satellite control rather than rendering broken tiles.

This module is deliberately separate from the existing `tools/imagery.py`, which
is about photo severity and is switched off.

**Frontend: `components/map/SatelliteLayers.tsx` + changes to `ReliefMap.tsx`**

- `ReliefMap` already builds a MapLibre style with a `type: "raster"` source and
  already carries a toggle (`showHeat`). Satellite layers follow that shape.
- **`maxzoom` on each raster source must match its TileMatrixSet level** --
  Level9 to 9, Level6 to 6, Level12 to 12. The map's default zoom is 10, which
  already exceeds Level9, so without a correct `maxzoom` MapLibre will request
  tiles that do not exist and the layer will vanish exactly when the coordinator
  zooms in to look at it. This is the single easiest thing to get wrong here.
- Layers are mutually exclusive with a single "off" state, drawn beneath the
  request pins and above the OSM basemap, with opacity control.
- NASA GIBS attribution is rendered in the control bar. This is required by the
  usage policy and is not decoration -- the same standing the OSM attribution
  already has in this component.
- Each layer's control shows the date actually being displayed, not "today".

## Phase 2 -- River discharge

**`agent/tools/river.py`**, shaped exactly like `weather.py`: a plain
`discharge_for(lat, lon)` plus a `@tool` wrapper, explicit timeout, failures
returned as values.

Returns `current_m3s`, `mean_m3s`, `max_next_7d_m3s`, a `trend` of
rising/falling/steady derived from the 7-day forward series, and
`model: "GloFAS v4 via Open-Meteo"`.

New setting `open_meteo_flood_base`. 3-hour TTL cache.

## Phase 3 -- ReliefWeb repair

`config.py` currently points `reliefweb_base` at `api.reliefweb.int/v1`, which
returns 410 Gone. `situation_context()` therefore returns `available: false` on
every call today, silently.

Pointing at v2 alone does not fix it, because v2 rejects unapproved appnames
with a 403. So:

- `reliefweb_base` moves to `https://api.reliefweb.int/v2`.
- New optional setting `reliefweb_appname`.
- When set, the JSON API is used and the result reports `source: "api"`.
- When unset, the tool falls back to the keyless RSS endpoint
  (`reliefweb.int/updates/rss.xml?search=...`, verified 200 with 20 items),
  parsed with stdlib `xml.etree`, reporting `source: "rss"`.

This works today with zero registration and upgrades cleanly if an appname is
later approved. Requesting one is a manual form with email review, so it cannot
be a blocking dependency.

## Phase 4 -- NDMA daily situation report

The most fragile component in this spec, and sequenced last for that reason.

**`agent/tools/ndma.py`**, in two steps so the expensive one is avoidable:

- `latest_sitrep()` -- fetches the listing at `ndma.gov.pk/sitreps?cat_id=3`
  (36 KB), extracts report number, date and PDF URL. Cheap.
- `district_figures(district)` -- if the cached report number matches the
  listing, returns cache and downloads nothing. Otherwise fetches the 3.1 MB PDF
  and parses the district table.

Cached in the store as `SITREP#<number>`. NDMA publishes once a day, so steady
state is roughly one 3 MB fetch per day. Scraping a government site is a
courtesy relationship and that budget is what keeps it defensible.

`pdfplumber` goes in a **new optional extra** (`uv sync --extra ndma`), not base
dependencies -- layout-aware extraction is required because the PDF's glyph runs
are fragmented and `pypdf` would mangle the tables. Without the extra installed,
the tool returns `available: false, reason: "pdfplumber is not installed"`.

**Failure behaviour, as chosen:** when the PDF is fetched but the district table
cannot be found, the console shows

> NDMA Sitrep No. 69 (02 Sep 2026) fetched, but the district table could not be
> parsed.

with a link to the source PDF. It does not fall back to an older report and does
not render a plausible-looking zero. NDMA will eventually reformat the sitrep;
this path is a tested first-class case, not an afterthought.

## API surface

New `api/routes_context.py`:

- `GET /context` -- `{river, ndma, reliefweb, fetched_at}`, each block carrying
  `available`, `source_url`, `as_of`. Optional `?lat=&lon=`, defaulting to the
  district centroid.
- `GET /context/imagery` -- the GIBS layer/date manifest from Phase 1.

## Frontend surface

- `components/context/SituationPanel.tsx` in the right column above
  `AgentActivity`. Each source renders its own state independently: a value, or
  the failure sentence plus a source link.
- Request detail page: river discharge beside rainfall, since both are
  per-location.
- About page: full provenance -- source names, URLs, fetch times, and the
  explicit statement that none of it touches the urgency score.
- **The demo banner must change.** It currently reads "Demo data -- synthetic
  requests modelled on published flood reporting. No real people." With a real
  NDMA panel and real satellite imagery beside it, that sentence becomes
  misleading about its own neighbours. It must distinguish the synthetic queue
  from the real situation layer.

## Testing

All mocked with `respx`, already a dev dependency. No test hits the live network.

- `test_river.py` -- trend computation, failure to `available: false`.
- `test_reliefweb.py` -- v2 JSON path, RSS fallback path, 403 path.
- `test_ndma.py` -- listing parse from an HTML fixture; **fetched-but-
  unparseable** path; cache hit on unchanged report number; missing `pdfplumber`.
- `test_imagery_layers.py` -- capabilities parse from a trimmed XML fixture;
  6-hour cache; failure to `available: false`.
- `test_scoring.py` addition -- score a request with and without every context
  block present, assert identical urgency. This is the test that keeps
  constraint 2 true.

## Config additions

`open_meteo_flood_base`, `gibs_base`, `ndma_sitrep_base`, `reliefweb_appname`,
`situation_district`. `reliefweb_base` changes from v1 to v2.

## Risks

1. **NDMA reformats the sitrep.** Certain to happen eventually. Mitigated by the
   loud-failure path being a tested case rather than a hope.
2. **Cloud hides the flood layer when it matters most.** Inherent to optical
   sensing. Mitigated only by saying so on screen; there is no technical fix
   available without reliable SAR coverage.
3. **A coordinator over-trusts a 250 m pixel.** Mitigated by the review rule
   above and by never surfacing satellite state on an individual request row.
4. **GIBS capabilities is 5.8 MB.** Mitigated by the 6-hour cache. If the link
   makes even that painful, the extracted manifest can be committed as a
   fallback fixture with its fetch date shown.

## Found during implementation

Two defects that only running against live sources exposed. Both are recorded
because both were the kind that produce confident, plausible, wrong output.

1. **A second province table overwrote the first.** Report 69 carries a relief
   items table later in the document whose rows have the identical six-number
   shape as the cumulative damage table — `KP 0 0 0 0 192 1298`. A
   document-wide scan let it win, and the console reported 192 houses damaged
   in KP where the report said 370. The original fixture was four pages and did
   not contain the clash, so the tests passed. Fixed by scoping the scan to the
   anchored table and stopping at its own Grand Total; the fixture now includes
   the colliding page, and a regression test names it.

2. **The cloud caveat was inverted.** See the corrected note above.

3. **Six of ten layers publish nothing over this district.** Fetching the exact
   tile URLs the console hands MapLibre showed 404 for all three VIIRS flood
   products, Aqua true colour and Sentinel-1 — while every one of their
   capabilities entries named 2026-09-03. A declared layer is not a served
   layer. Selecting one drew nothing, which is indistinguishable from a layer
   that looked and found no water: the exact failure this component exists to
   prevent, reintroduced one level up. Fixed by probing one real tile per layer
   at the district centre when the manifest is built (concurrent, cached for six
   hours behind the manifest), recording `covers_district` as true/false/unknown
   — unknown for a probe that could not run, because a failed probe is not
   evidence of absence. The picker annotates those layers and the map states
   plainly that nothing will draw.

Both were caught by verifying against the live sources rather than by the test
suite, which is the argument for doing that before calling the work done.

## Sequencing

Phase 1 (satellite) first: keyless, verified working today, fits the existing
raster pattern, largest visible payoff. Then Phase 2 (river, trivial), Phase 3
(ReliefWeb, a bug fix), and Phase 4 (NDMA) last, because it is the only part
that can break through no fault of this repository.
