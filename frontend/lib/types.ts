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
