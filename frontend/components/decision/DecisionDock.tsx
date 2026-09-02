"use client";

import type { Decision } from "@/lib/types";
import { DecisionCard } from "./DecisionCard";

/**
 * The dock. Collapsed to a single amber bar when nothing is pending; rises when
 * a decision arrives. This is the one place in the console where motion is
 * allowed, and it happens once per decision, triggered by a real event.
 */
export function DecisionDock({
  decisions,
  activeIndex,
  onResolve,
  onCycle,
  busy,
}: {
  decisions: Decision[];
  activeIndex: number;
  onResolve: (decisionId: string, optionId: string, note?: string) => void;
  onCycle: () => void;
  busy: boolean;
}) {
  if (decisions.length === 0) {
    return (
      <div className="flex items-center justify-between border-t border-line bg-surface px-4 py-2">
        <span className="text-ink-muted">Nothing needs you right now.</span>
        <span className="data text-ink-muted">j / k to move · Enter to open</span>
      </div>
    );
  }

  const active = decisions[Math.min(activeIndex, decisions.length - 1)];
  if (!active) return null;

  return (
    <div
      className="dock-rise border-t-2 border-signal bg-surface"
      style={{ maxHeight: "var(--dock-h)" }}
      role="region"
      aria-label="Decision required"
    >
      <div className="flex items-center justify-between border-b border-line px-4 py-1.5">
        <span className="text-12 font-medium uppercase tracking-wide text-signal">
          {decisions.length === 1
            ? "1 decision waiting"
            : `${decisions.length} decisions waiting`}
        </span>
        <div className="flex items-center gap-3">
          <span className="data text-ink-muted">1 / 2 / 3 to answer · Esc to dismiss</span>
          {decisions.length > 1 ? (
            <button
              type="button"
              onClick={onCycle}
              className="border border-line px-2 py-0.5 text-12 text-ink hover:bg-surface-2"
            >
              Next
            </button>
          ) : null}
        </div>
      </div>

      <div style={{ maxHeight: "calc(var(--dock-h) - 32px)" }} className="overflow-y-auto">
        <DecisionCard
          decision={active}
          busy={busy}
          onResolve={(optionId, note) => onResolve(active.id, optionId, note)}
        />
      </div>
    </div>
  );
}
