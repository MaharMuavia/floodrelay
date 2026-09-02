"use client";

import { useEffect, useMemo, useRef } from "react";
import type { BoardRow } from "@/lib/types";
import { RequestRow } from "./RequestRow";

const ROW_HEIGHT = 60;
const OVERSCAN = 6;

/**
 * The triage queue.
 *
 * Virtualised because a district in a bad week produces hundreds of rows and a
 * laptop on bad wifi should not be rendering all of them. The windowing is done
 * by hand rather than with a library: the row height is fixed and the list is
 * flat, so the whole implementation is a slice and two spacers.
 */
export function QueueColumn({
  rows,
  selectedId,
  pulsingIds,
  onSelect,
  onOpen,
  scrollTop,
  onScroll,
  viewportHeight,
}: {
  rows: BoardRow[];
  selectedId: string | null;
  pulsingIds: Set<string>;
  onSelect: (id: string) => void;
  onOpen: (id: string) => void;
  scrollTop: number;
  onScroll: (top: number) => void;
  viewportHeight: number;
}) {
  const containerRef = useRef<HTMLDivElement>(null);

  const { start, end, offsetTop } = useMemo(() => {
    const first = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - OVERSCAN);
    const visible = Math.ceil(viewportHeight / ROW_HEIGHT) + OVERSCAN * 2;
    return {
      start: first,
      end: Math.min(rows.length, first + visible),
      offsetTop: first * ROW_HEIGHT,
    };
  }, [scrollTop, viewportHeight, rows.length]);

  // Keep the keyboard selection in view as j/k walk the list.
  useEffect(() => {
    if (!selectedId || !containerRef.current) return;
    const index = rows.findIndex((r) => r.id === selectedId);
    if (index < 0) return;
    const top = index * ROW_HEIGHT;
    const el = containerRef.current;
    if (top < el.scrollTop) el.scrollTop = top;
    else if (top + ROW_HEIGHT > el.scrollTop + el.clientHeight) {
      el.scrollTop = top + ROW_HEIGHT - el.clientHeight;
    }
  }, [selectedId, rows]);

  if (rows.length === 0) {
    return (
      <div className="flex h-full items-start justify-center p-6">
        <p className="max-w-[24ch] text-center text-ink-muted">
          No open requests. New messages appear here as they arrive.
        </p>
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      role="listbox"
      aria-label="Triage queue"
      tabIndex={0}
      className="h-full overflow-y-auto outline-none"
      onScroll={(e) => onScroll(e.currentTarget.scrollTop)}
    >
      <div style={{ height: offsetTop }} />
      {rows.slice(start, end).map((row) => (
        <RequestRow
          key={row.id}
          row={row}
          selected={row.id === selectedId}
          pulsing={pulsingIds.has(row.id)}
          onSelect={() => onSelect(row.id)}
          onOpen={() => onOpen(row.id)}
        />
      ))}
      <div style={{ height: Math.max(0, (rows.length - end) * ROW_HEIGHT) }} />
    </div>
  );
}
