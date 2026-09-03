"use client";

import { useQuery } from "@tanstack/react-query";
import maplibregl, { type Map as MapLibreMap } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { kindColor } from "@/lib/format";
import type { BoardRow, HeatmapCell, ResourceRow, SatelliteLayer } from "@/lib/types";

const NOWSHERA: [number, number] = [71.9747, 34.0151];

/**
 * MapLibre with OpenStreetMap raster tiles. No Mapbox token, no billing.
 *
 * OSM attribution is rendered in the control bar, as the tile usage policy
 * requires -- it is not optional and it is not decoration.
 */
export function ReliefMap({
  rows,
  resources,
  heatmap,
  selectedId,
  pulsingIds,
  onSelect,
}: {
  rows: BoardRow[];
  resources: ResourceRow[];
  heatmap: HeatmapCell[];
  selectedId: string | null;
  pulsingIds: Set<string>;
  onSelect: (id: string) => void;
}) {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<MapLibreMap | null>(null);
  const markers = useRef<maplibregl.Marker[]>([]);
  const [ready, setReady] = useState(false);
  const [showHeat, setShowHeat] = useState(false);
  const [satelliteId, setSatelliteId] = useState<string>("");
  const [satelliteOpacity, setSatelliteOpacity] = useState(0.7);

  // The manifest tells us which date GIBS has actually published. Asking for
  // "today" when today is not out yet returns 404s for every tile, and MapLibre
  // reports that by drawing nothing at all -- which reads as "no flood".
  const imagery = useQuery({ queryKey: ["imagery"], queryFn: () => api.imagery() });
  const satelliteLayers: SatelliteLayer[] = imagery.data?.available
    ? imagery.data.layers
    : [];
  const activeLayer = satelliteLayers.find((l) => l.id === satelliteId) ?? null;

  useEffect(() => {
    if (!container.current || map.current) return;

    map.current = new maplibregl.Map({
      container: container.current,
      style: {
        version: 8,
        sources: {
          osm: {
            type: "raster",
            tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
            tileSize: 256,
            attribution: "© OpenStreetMap contributors",
          },
        },
        layers: [
          { id: "bg", type: "background", paint: { "background-color": "#eef2f6" } },
          {
            id: "osm",
            type: "raster",
            source: "osm",
            // The base map is reference, not the subject. On a light ground it
            // is held back by desaturating and lifting the blacks rather than
            // by dimming: dimming a light map only makes it muddy, and the pins
            // need a pale, low-contrast field to sit on.
            paint: {
              "raster-opacity": 0.85,
              "raster-saturation": -0.55,
              "raster-brightness-min": 0.12,
            },
          },
        ],
      },
      center: NOWSHERA,
      zoom: 10,
      attributionControl: false,
    });

    map.current.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    map.current.on("load", () => setReady(true));

    return () => {
      map.current?.remove();
      map.current = null;
    };
  }, []);

  // Heatmap layer, toggled.
  //
  // `load` fires before the style is necessarily usable -- sprites and glyphs
  // are still arriving -- and touching sources or layers before then throws
  // "Style is not done loading". That is not a hot-reload artefact: it is what
  // a real client on a slow connection hits, which is this project's stated
  // operating condition. So wait for the style itself, not just for `load`.
  useEffect(() => {
    const m = map.current;
    if (!m || !ready) return;

    const geojson = {
      type: "FeatureCollection" as const,
      features: heatmap.map((cell) => ({
        type: "Feature" as const,
        properties: { weight: cell.weight },
        geometry: { type: "Point" as const, coordinates: [cell.lon, cell.lat] },
      })),
    };

    const apply = () => {
      // The map can be torn down between the event firing and this running.
      if (!map.current || !map.current.isStyleLoaded()) return;
      applyHeatLayer(map.current, geojson, showHeat);
    };

    if (m.isStyleLoaded()) {
      apply();
    } else {
      m.once("styledata", apply);
      return () => {
        m.off("styledata", apply);
      };
    }
  }, [heatmap, ready, showHeat]);

  function applyHeatLayer(
    m: MapLibreMap,
    geojson: GeoJSON.FeatureCollection,
    visible: boolean,
  ) {
    const existing = m.getSource("heat") as maplibregl.GeoJSONSource | undefined;
    if (existing) {
      existing.setData(geojson);
    } else {
      m.addSource("heat", { type: "geojson", data: geojson });
      m.addLayer({
        id: "heat",
        type: "heatmap",
        source: "heat",
        paint: {
          "heatmap-weight": ["interpolate", ["linear"], ["get", "weight"], 0, 0, 3, 1],
          "heatmap-radius": 34,
          "heatmap-opacity": 0.7,
          "heatmap-color": [
            "interpolate",
            ["linear"],
            ["heatmap-density"],
            0, "rgba(0,0,0,0)",
            0.3, "#3e9bd6",
            0.65, "#ffc24b",
            1, "#ff6b57",
          ],
        },
      });
    }
    m.setLayoutProperty("heat", "visibility", visible ? "visible" : "none");
  }

  // Satellite raster, swapped rather than mutated: a raster source's tile URL
  // template cannot be changed in place, so switching layers means removing and
  // re-adding both.
  useEffect(() => {
    const m = map.current;
    if (!m || !ready) return;

    const apply = () => {
      const live = map.current;
      if (!live || !live.isStyleLoaded()) return;

      if (live.getLayer("satellite")) live.removeLayer("satellite");
      if (live.getSource("satellite")) live.removeSource("satellite");
      if (!activeLayer) return;

      live.addSource("satellite", {
        type: "raster",
        tiles: [activeLayer.tile_url],
        tileSize: 256,
        // Load-bearing. The map opens at zoom 10 and the flood products stop at
        // Level 9; without this MapLibre requests tiles that do not exist and
        // the layer vanishes exactly when someone zooms in to look at it.
        ...(activeLayer.max_zoom !== null ? { maxzoom: activeLayer.max_zoom } : {}),
        attribution: imagery.data?.attribution ?? "NASA EOSDIS GIBS",
      });
      live.addLayer(
        {
          id: "satellite",
          type: "raster",
          source: "satellite",
          paint: { "raster-opacity": satelliteOpacity },
        },
        // Under the heatmap when that is on, always under the DOM pins.
        live.getLayer("heat") ? "heat" : undefined,
      );
    };

    if (m.isStyleLoaded()) {
      apply();
    } else {
      m.once("styledata", apply);
      return () => {
        m.off("styledata", apply);
      };
    }
  }, [activeLayer, imagery.data?.attribution, ready, satelliteOpacity]);

  // Pins. Rebuilt on change: the counts here are tens, not thousands.
  useEffect(() => {
    const m = map.current;
    if (!m || !ready) return;

    markers.current.forEach((marker) => marker.remove());
    markers.current = [];

    for (const row of rows) {
      if (row.lat === null || row.lon === null) continue;

      const el = document.createElement("button");
      el.type = "button";
      el.setAttribute("aria-label", `Request ${row.id}`);
      const size = row.id === selectedId ? 16 : 11;
      el.style.cssText = `width:${size}px;height:${size}px;border-radius:50%;cursor:pointer;padding:0;background:${kindColor(row.kind)};border:1.5px solid ${row.id === selectedId ? "var(--ink)" : "rgba(14,32,41,.55)"};`;
      if (pulsingIds.has(row.id)) el.classList.add("pin-pulse");
      el.addEventListener("click", (e) => {
        e.stopPropagation();
        onSelect(row.id);
      });

      markers.current.push(
        new maplibregl.Marker({ element: el }).setLngLat([row.lon, row.lat]).addTo(m),
      );
    }

    for (const resource of resources) {
      const el = document.createElement("div");
      el.title = `${resource.name} (${resource.status})`;
      el.style.cssText = `width:10px;height:10px;background:${resource.status === "available" ? "var(--stable)" : "var(--ink-muted)"};border:1.5px solid rgba(14,32,41,.55);transform:rotate(45deg);`;
      markers.current.push(
        new maplibregl.Marker({ element: el })
          .setLngLat([resource.lon, resource.lat])
          .addTo(m),
      );
    }
  }, [rows, resources, selectedId, pulsingIds, ready, onSelect]);

  return (
    <div className="relative h-full w-full">
      <div ref={container} className="h-full w-full" />

      {/* The caveat sits with the layer, not in a help page. A flood layer read
          without it reads backwards: blank means cloud, not absence of water. */}
      {activeLayer ? (
        <div className="pointer-events-none absolute inset-x-0 top-0 p-2">
          <div className="pointer-events-auto max-w-[52ch] border border-line bg-surface/95 px-2 py-1.5 text-12 shadow-sm">
            <div className="text-ink">
              {activeLayer.title} · {activeLayer.latest}
            </div>
            {/* The whole point of the coverage probe. A layer that draws
                nothing is indistinguishable from a layer that looked and found
                no water, so when GIBS publishes nothing here the map says it
                rather than showing an empty overlay. */}
            {activeLayer.covers_district === false ? (
              <p className="mt-0.5 text-signal">
                No tiles published for this layer over this district on{" "}
                {activeLayer.latest}. Nothing will draw — this is missing data,
                not an absence of water.
              </p>
            ) : null}
            <p className="mt-0.5 text-ink-muted">{activeLayer.caveat}</p>

            {/* The key is not decoration. Measured over this district, 54-64%
                of a flood tile is grey "Insufficient Data" and under 0.3% is
                any water class. Without the swatches the grey reads as
                coverage and the clear gaps read as calm, which is the picture
                exactly inverted. */}
            {activeLayer.legend.length > 0 ? (
              <ul className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1">
                {activeLayer.legend.map((entry) => (
                  <li key={entry.rgb} className="flex items-center gap-1 text-ink-muted">
                    <span
                      aria-hidden
                      className="inline-block h-2 w-2 border border-line"
                      style={{ background: `rgb(${entry.rgb})` }}
                    />
                    {entry.label}
                  </li>
                ))}
              </ul>
            ) : null}

            <label className="mt-1.5 flex items-center gap-2 text-ink-muted">
              Opacity
              <input
                type="range"
                min={0.2}
                max={1}
                step={0.05}
                value={satelliteOpacity}
                onChange={(e) => setSatelliteOpacity(Number(e.target.value))}
                className="h-1 w-24"
                aria-label="Satellite layer opacity"
              />
            </label>
          </div>
        </div>
      ) : null}

      <div className="pointer-events-none absolute inset-x-0 bottom-0 flex items-end justify-between gap-2 p-2">
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setShowHeat((v) => !v)}
            className="pointer-events-auto border border-line bg-surface px-2 py-1 text-12 text-ink hover:bg-surface-2"
          >
            {showHeat ? "Hide heatmap" : "Show heatmap"}
          </button>

          {satelliteLayers.length > 0 ? (
            <select
              aria-label="Satellite layer"
              value={satelliteId}
              onChange={(e) => setSatelliteId(e.target.value)}
              className="pointer-events-auto border border-line bg-surface px-2 py-1 text-12 text-ink"
            >
              <option value="">No satellite layer</option>
              {satelliteLayers.map((layer) => (
                <option key={layer.id} value={layer.id}>
                  {layer.title} ({layer.latest})
                  {layer.covers_district === false ? " — no tiles here" : ""}
                </option>
              ))}
            </select>
          ) : null}
        </div>

        <span className="pointer-events-auto bg-surface/85 px-1.5 py-0.5 text-right text-12 text-ink-muted">
          ©{" "}
          <a
            href="https://www.openstreetmap.org/copyright"
            target="_blank"
            rel="noreferrer"
            className="underline"
          >
            OpenStreetMap
          </a>{" "}
          contributors
          {/* NASA's usage policy requires attribution wherever their imagery is
              shown. It has the same standing here as OSM's, not less. */}
          {activeLayer ? (
            <>
              {" · "}
              {imagery.data?.attribution ?? "Imagery courtesy NASA EOSDIS GIBS"}
            </>
          ) : null}
        </span>
      </div>
    </div>
  );
}
