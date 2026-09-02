"use client";

import maplibregl, { type Map as MapLibreMap } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useEffect, useRef, useState } from "react";
import { kindColor } from "@/lib/format";
import type { BoardRow, HeatmapCell, ResourceRow } from "@/lib/types";

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
          { id: "bg", type: "background", paint: { "background-color": "#0a1620" } },
          {
            id: "osm",
            type: "raster",
            source: "osm",
            // The base map is reference, not the subject. Dimming it keeps the
            // pins legible against a night palette.
            paint: { "raster-opacity": 0.55, "raster-saturation": -0.6, "raster-brightness-max": 0.8 },
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
      el.style.cssText = `width:${size}px;height:${size}px;border-radius:50%;cursor:pointer;padding:0;background:${kindColor(row.kind)};border:1.5px solid ${row.id === selectedId ? "var(--ink)" : "rgba(10,22,32,.85)"};`;
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
      el.style.cssText = `width:10px;height:10px;background:${resource.status === "available" ? "var(--stable)" : "var(--ink-muted)"};border:1.5px solid rgba(10,22,32,.85);transform:rotate(45deg);`;
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

      <div className="pointer-events-none absolute inset-x-0 bottom-0 flex items-end justify-between p-2">
        <button
          type="button"
          onClick={() => setShowHeat((v) => !v)}
          className="pointer-events-auto border border-line bg-surface px-2 py-1 text-12 text-ink hover:bg-surface-2"
        >
          {showHeat ? "Hide heatmap" : "Show heatmap"}
        </button>
        <span className="pointer-events-auto bg-depth/80 px-1.5 py-0.5 text-12 text-ink-muted">
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
        </span>
      </div>
    </div>
  );
}
