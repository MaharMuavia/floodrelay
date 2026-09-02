/** Typed fetch wrappers. One place that knows the API base URL. */

import type {
  Board,
  Decision,
  AuditEvent,
  HeatmapCell,
  RequestDetail,
} from "./types";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8080";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
      cache: "no-store",
    });
  } catch {
    // Bad wifi is the operating condition, so say what happened plainly.
    throw new ApiError("Couldn't reach the server. Check the connection.", 0);
  }

  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* keep the status line */
    }
    throw new ApiError(detail, response.status);
  }

  return (await response.json()) as T;
}

export const api = {
  board: (status?: string) =>
    request<Board>(`/board${status ? `?status=${status}` : ""}`),

  requestDetail: (id: string) => request<RequestDetail>(`/requests/${id}`),

  decisions: (open = true) =>
    request<{ decisions: Decision[]; count: number }>(
      `/decisions?open=${open}`,
    ),

  resolveDecision: (
    id: string,
    body: { option_id: string; note?: string; lat?: number; lon?: number },
  ) =>
    request<{ decision_id: string; outcomes: string[] }>(
      `/decisions/${id}/resolve`,
      { method: "POST", body: JSON.stringify(body) },
    ),

  audit: (params?: { date?: string; request_id?: string }) => {
    const q = new URLSearchParams();
    if (params?.date) q.set("date", params.date);
    if (params?.request_id) q.set("request_id", params.request_id);
    const suffix = q.toString();
    return request<{ events: AuditEvent[]; count: number; note: string }>(
      `/audit${suffix ? `?${suffix}` : ""}`,
    );
  },

  heatmap: () => request<{ cells: HeatmapCell[]; cell_deg: number }>("/map/heatmap"),

  formula: () =>
    request<{
      weights: Record<string, number>;
      kind_weights: Record<string, number>;
      recency_window_hours: number;
      note: string;
    }>("/urgency/formula"),

  demoInfo: () =>
    request<{ synthetic: boolean; requests: number; resources: number; note: string; demo_mode: boolean }>(
      "/demo/info",
    ),

  health: () =>
    request<{
      status: string;
      store: string;
      models: Record<string, string>;
      demo_mode: boolean;
      checks: Record<string, unknown>;
    }>("/healthz"),

  replay: (speed = 1) =>
    request<{ accepted: number; request_ids: string[] }>("/demo/replay", {
      method: "POST",
      body: JSON.stringify({ speed }),
    }),

  reset: () =>
    request<{ removed: number; resources: number }>("/demo/reset", {
      method: "POST",
    }),

  intake: (text: string, channel = "form") =>
    request<{ request_id: string; trace_id: string | null }>("/intake", {
      method: "POST",
      body: JSON.stringify({ text, channel }),
    }),
};
