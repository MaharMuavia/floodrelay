/** Formatting helpers. Terse by design: the queue is read, not studied. */

import type { BoardRow, NeedKind } from "./types";

export const KIND_COLOR: Record<NeedKind, string> = {
  rescue: "var(--rescue)",
  medical: "var(--medical)",
  food_water: "var(--water)",
  shelter: "var(--stable)",
  other: "var(--ink-muted)",
};

export const KIND_LABEL: Record<NeedKind, string> = {
  rescue: "Rescue",
  medical: "Medical",
  food_water: "Food and water",
  shelter: "Shelter",
  other: "Other",
};

export function kindColor(kind: NeedKind | null): string {
  return kind ? KIND_COLOR[kind] : "var(--ink-muted)";
}

/** Severity edge thickness: 2px at urgency 0, 8px at urgency 1. */
export function edgeWidth(urgency: number | null): number {
  const u = Math.max(0, Math.min(1, urgency ?? 0));
  return Math.round(2 + u * 6);
}

export function urgency(value: number | null): string {
  return value === null ? "--" : value.toFixed(2);
}

export function relativeTime(iso: string, now: number = Date.now()): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const seconds = Math.round((now - then) / 1000);
  if (seconds < 60) return `${Math.max(0, seconds)}s ago`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

export function clockTime(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? ""
    : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

/** Who is involved, in the order the decision card uses. */
export function whoLine(row: BoardRow): string {
  const bits: string[] = [];
  if (row.people_total) bits.push(`${row.people_total} people`);
  if (row.children) bits.push(`${row.children} children`);
  if (row.elderly) bits.push(`${row.elderly} elderly`);
  if (row.disabled) bits.push("1 unable to move unaided");
  if (row.pregnant) bits.push("1 pregnant");
  return bits.join(", ");
}

export function statusLabel(status: string): string {
  switch (status) {
    case "needs_decision":
      return "Needs you";
    case "new":
      return "New";
    case "processing":
      return "Working";
    case "matched":
      return "Matched";
    case "dispatched":
      return "Sent";
    case "duplicate":
      return "Duplicate";
    case "closed":
      return "Closed";
    default:
      return status;
  }
}

export function truncate(text: string, max = 120): string {
  return text.length <= max ? text : `${text.slice(0, max - 1)}…`;
}

/**
 * Nominatim returns the whole administrative hierarchy: "Kheshgi Payan,
 * Nowshera Tehsil, Nowshera District, Peshawar Division, Khyber Pakhtunkhwa,
 * Pakistan". In a dense queue that is noise -- the coordinator already knows
 * which district they are working. Keep the leading components, which are the
 * part that distinguishes one request from another, and expose the full string
 * as a tooltip.
 */
export function shortPlace(label: string | null, parts = 2): string {
  if (!label) return "";
  const pieces = label
    .split(",")
    .map((piece) => piece.trim())
    .filter(Boolean);
  return pieces.slice(0, parts).join(", ") || label;
}
