"use client";

import {
  KIND_LABEL,
  relativeTime,
  shortPlace,
  statusLabel,
  truncate,
  urgency,
  whoLine,
} from "@/lib/format";
import type { BoardRow } from "@/lib/types";
import { SeverityEdge } from "./SeverityEdge";

/**
 * One queue row. Flat by design: no card, no shadow, no per-row radius, one
 * hairline between rows. Hover is an instant background change, not a
 * transition -- a list of 28 rows that each ease on hover is noise.
 */
export function RequestRow({
  row,
  selected,
  pulsing,
  onSelect,
  onOpen,
}: {
  row: BoardRow;
  selected: boolean;
  pulsing: boolean;
  onSelect: () => void;
  onOpen: () => void;
}) {
  const needsHuman = row.status === "needs_decision";
  const muted = row.status === "duplicate" || row.status === "closed";
  const who = whoLine(row);

  return (
    <div
      role="option"
      aria-selected={selected}
      tabIndex={-1}
      onClick={onSelect}
      onDoubleClick={onOpen}
      className={[
        "flex items-stretch gap-0 border-b border-line cursor-default",
        "hover:bg-surface-2",
        selected ? "bg-surface-2" : "bg-transparent",
        pulsing ? "signal-pulse" : "",
      ].join(" ")}
      style={{ minHeight: "var(--row-h)" }}
    >
      <SeverityEdge urgency={row.urgency} kind={row.kind} dimmed={muted} />

      <div className="flex min-w-0 flex-1 items-center gap-3 px-3 py-2">
        <span
          className={`numeral shrink-0 tabular-nums ${muted ? "text-ink-muted" : "text-ink"}`}
          style={{ fontSize: "var(--t-15)" }}
        >
          {urgency(row.urgency)}
        </span>

        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-2">
            <span className="data shrink-0 text-ink-muted">{row.id}</span>
            <span className={`truncate ${muted ? "text-ink-muted" : "text-ink"}`}>
              {row.kind ? KIND_LABEL[row.kind] : "Reading…"}
              {who ? <span className="text-ink-muted"> · {who}</span> : null}
            </span>
          </div>
          <div className="truncate text-12 text-ink-muted">
            <span title={row.location_label ?? undefined}>
              {shortPlace(row.location_label) || "No confirmed location"}
            </span>{" "}
            · {truncate(row.summary, 70)}
          </div>
        </div>

        <div className="flex shrink-0 flex-col items-end gap-0.5">
          {/* Amber appears here and only here: this row is waiting on a person. */}
          {needsHuman ? (
            <span className="text-12 font-medium text-signal">Needs you</span>
          ) : (
            <span className="text-12 text-ink-muted">{statusLabel(row.status)}</span>
          )}
          <span className="data text-ink-muted">{relativeTime(row.received_at)}</span>
        </div>
      </div>
    </div>
  );
}
