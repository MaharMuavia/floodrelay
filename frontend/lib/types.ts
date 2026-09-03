/**
 * Shapes the API returns.
 *
 * These mirror the FastAPI response models. The backend publishes an OpenAPI
 * schema at /openapi.json; `npm run gen:types` regenerates this file from it so
 * the two do not drift. Hand-edits here will be overwritten.
 */

export type NeedKind = "rescue" | "medical" | "food_water" | "shelter" | "other";

export type RequestStatus =
  | "new"
  | "processing"
  | "needs_decision"
  | "matched"
  | "dispatched"
  | "closed"
  | "duplicate";

export type DecisionKind =
  | "life_safety"
  | "low_confidence_location"
  | "resource_conflict"
  | "possible_duplicate"
  // The agent could not process the message. Still answerable: retry, or take
  // it over by hand. A request must never sit amber with nothing behind it.
  | "processing_failed";

export interface BoardRow {
  id: string;
  status: RequestStatus;
  urgency: number | null;
  kind: NeedKind | null;
  received_at: string;
  channel: string;
  summary: string;
  people_total: number | null;
  children: number | null;
  elderly: number | null;
  disabled: boolean | null;
  pregnant: boolean | null;
  water_level_note: string | null;
  lat: number | null;
  lon: number | null;
  location_label: string | null;
  location_confidence: number | null;
  matched_resource_id: string | null;
  duplicate_of: string | null;
  photo_key: string | null;
  photo_severity: number | null;
  trace_id: string | null;
}

export interface ResourceRow {
  id: string;
  name: string;
  kind: string;
  status: "available" | "assigned" | "offline";
  capacity: number;
  lat: number;
  lon: number;
  current_assignment: string | null;
}

export interface BoardCounts {
  total: number;
  open: number;
  needs_decision: number;
  dispatched: number;
  duplicate: number;
}

export interface Board {
  requests: BoardRow[];
  counts: BoardCounts;
  resources: ResourceRow[];
}

export interface DecisionOption {
  id: string;
  label: string;
  request_id: string | null;
  resource_id: string | null;
  is_dispatch: boolean;
  facts: Record<string, string>;
}

export interface Decision {
  id: string;
  kind: DecisionKind;
  request_ids: string[];
  heading: string;
  reasoning: string;
  recommendation_option_id: string | null;
  options: DecisionOption[];
  created_at: string;
  resolved_at: string | null;
  resolved_by: string | null;
  is_open: boolean;
  trace_id: string | null;
}

export interface AuditEvent {
  id: string;
  ts: string;
  actor: "agent" | "coordinator" | "system";
  node: string | null;
  tool: string | null;
  request_id: string | null;
  input_digest: string;
  output_digest: string;
  latency_ms: number | null;
  tokens: number | null;
  error: string | null;
  decision_card_id: string | null;
  trace_id: string | null;
}

export interface UrgencyBreakdown {
  kind: number;
  vulnerability: number;
  photo: number;
  water_level: number;
  recency: number;
  total: number;
}

export interface RequestDetail extends BoardRow {
  raw_text: string;
  need: Record<string, unknown> | null;
  location: Record<string, unknown> | null;
  node_history: string[];
  geo_attempts: number;
  urgency_breakdown: UrgencyBreakdown;
  urgency_weights: Record<string, number>;
  audit: AuditEvent[];
}

export interface HeatmapCell {
  lat: number;
  lon: number;
  weight: number;
  count: number;
}

/** Events pushed over SSE. The board animates directly off these. */
export type AgentEvent =
  | { type: "node_start"; request_id: string; node: string; ts: string }
  | { type: "tool_call"; request_id: string; tool: string; summary: string; ts: string }
  | {
      type: "node_complete";
      request_id: string;
      node: string;
      latency_ms?: number;
      result?: Record<string, unknown> | null;
      ts: string;
    }
  | {
      type: "decision_required";
      decision_id: string;
      kind: DecisionKind;
      request_ids: string[];
      ts: string;
    }
  | {
      type: "request_updated";
      request_id: string;
      status: RequestStatus;
      urgency: number | null;
      ts: string;
    }
  | { type: "request_received"; request_id: string; channel: string; ts: string }
  | { type: "decision_resolved"; decision_id: string; option_id: string; ts: string }
  | { type: "heartbeat"; ts: string }
  | { type: string; ts: string; [key: string]: unknown };

export type ConnectionState = "connecting" | "live" | "retrying" | "offline";

// --- Situation context ------------------------------------------------------
//
// Real data about the real flood, shown around the synthetic queue. Every block
// carries its own `available` flag because each source fails independently, and
// a source that is down must degrade its own tile rather than the console.

export interface SourceBlock {
  available: boolean;
  error?: string | null;
  source_url?: string | null;
}

export interface RiverBlock extends SourceBlock {
  current_m3s: number | null;
  max_next_7d_m3s?: number | null;
  trend?: "rising" | "falling" | "steady" | "unknown";
  as_of?: string | null;
  units?: string;
  model?: string;
}

export interface RainfallBlock extends SourceBlock {
  recent_48h_mm?: number | null;
  next_48h_mm?: number | null;
  max_hourly_mm?: number | null;
}

export interface DamageFigures {
  roads_km: number;
  bridges: number;
  houses_full: number;
  houses_partial: number;
  houses_total: number;
  livestock: number;
}

export interface NdmaBlock extends SourceBlock {
  report_number?: number | null;
  report_date?: string | null;
  report_url?: string | null;
  remedy?: string | null;
  province_name?: string;
  province?: DamageFigures | null;
  district_name?: string;
  district?: { houses_partial: number; houses_full: number; houses_total: number; livestock: number } | null;
  district_reported?: boolean;
  districts_reported?: string[];
}

export interface ReliefWebBlock extends SourceBlock {
  source?: "api" | "rss";
  reports?: { title: string; url: string | null; date: string | null }[];
}

export interface LegendEntry {
  /** "r,g,b" exactly as NASA publishes it in the layer's colormap. */
  rgb: string;
  label: string;
}

export interface SatelliteLayer {
  id: string;
  title: string;
  group: "flood" | "imagery" | "rain" | string;
  caveat: string;
  latest: string;
  tile_url: string;
  max_zoom: number | null;
  format: string;
  legend: LegendEntry[];
  /**
   * Whether the layer actually served a tile over this district, established by
   * asking for one. GIBS declaring a layer for today does not mean it publishes
   * anything here: measured over Nowshera, six of ten curated layers 404 while
   * their capabilities entries all named today. `null` means the probe could not
   * run, which is not the same as "no coverage".
   */
  covers_district: boolean | null;
}

export interface ImageryBlock extends SourceBlock {
  layers: SatelliteLayer[];
  unavailable?: string[];
  attribution?: string;
  stale?: boolean;
  cached?: boolean;
}

export interface GdacsAlert {
  level: "Red" | "Orange" | "Green" | string;
  country: string;
  iso3: string;
  title: string;
  summary: string;
  url: string;
  event_id: string;
  from_date: string;
  to_date: string;
  lat: number | null;
  lon: number | null;
}

export interface GdacsBlock extends SourceBlock {
  alerts: GdacsAlert[];
  counts: Record<string, number>;
  /** Alerts for the country this console coordinates, called out separately. */
  here: GdacsAlert[];
  country?: string;
  total?: number;
}

export interface SituationContext {
  district: string;
  province: string;
  lat: number;
  lon: number;
  river: RiverBlock;
  rainfall: RainfallBlock;
  ndma: NdmaBlock;
  reliefweb: ReliefWebBlock;
  gdacs: GdacsBlock;
  imagery: ImageryBlock;
  fetched_at: string;
  note: string;
}
