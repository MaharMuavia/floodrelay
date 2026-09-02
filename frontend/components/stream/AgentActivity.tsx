"use client";

import { clockTime } from "@/lib/format";
import { isActivity } from "@/lib/sse";
import type { AgentEvent, ConnectionState } from "@/lib/types";

/**
 * The live agent feed, newest at top.
 *
 * This is where the console earns trust: the coordinator can watch the agent
 * work and see which tool produced which answer, rather than being handed a
 * conclusion. Tool calls are indented under the node that made them.
 */
export function AgentActivity({
  events,
  state,
}: {
  events: AgentEvent[];
  state: ConnectionState;
}) {
  const rows = events.filter(isActivity).slice(0, 120);

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-baseline justify-between border-b border-line px-3 py-2">
        <h2 className="text-12 uppercase tracking-wide text-ink-muted">Agent activity</h2>
        <ConnectionDot state={state} />
      </header>

      {rows.length === 0 ? (
        <p className="p-3 text-ink-muted">
          {state === "live"
            ? "Waiting for the next message."
            : "Not connected to the event stream."}
        </p>
      ) : (
        <ol className="flex-1 overflow-y-auto">
          {rows.map((event, index) => (
            <ActivityLine key={`${event.ts}-${index}`} event={event} />
          ))}
        </ol>
      )}
    </div>
  );
}

/** Read an optional string field without narrowing the whole union. */
function textField(event: AgentEvent, key: string): string | null {
  const value = (event as Record<string, unknown>)[key];
  return typeof value === "string" ? value : null;
}

function ActivityLine({ event }: { event: AgentEvent }) {
  const isTool = event.type === "tool_call";
  const isDecision = event.type === "decision_required";
  const requestId = textField(event, "request_id");

  let body: string;
  if (isTool) {
    body = `${textField(event, "tool") ?? "tool"} — ${textField(event, "summary") ?? ""}`;
  } else if (event.type === "node_start") {
    body = textField(event, "node") ?? "node";
  } else if (event.type === "node_complete") {
    body = `${textField(event, "node") ?? "node"} done`;
  } else if (isDecision) {
    body = `needs a decision: ${(textField(event, "kind") ?? "").replace(/_/g, " ")}`;
  } else if (event.type === "decision_resolved") {
    body = `you answered ${textField(event, "option_id") ?? ""}`;
  } else {
    body = event.type.replace(/_/g, " ");
  }

  return (
    <li
      className={[
        "flex items-baseline gap-2 border-b border-line/50 px-3 py-1",
        isTool ? "pl-7" : "",
        // Amber only when a human is needed.
        isDecision ? "text-signal" : "text-ink",
      ].join(" ")}
    >
      <span className="data shrink-0 text-ink-muted">{clockTime(event.ts)}</span>
      {isTool ? <span className="shrink-0 text-ink-muted">↳</span> : null}
      <span className="min-w-0 flex-1 truncate">{body}</span>
      {requestId ? <span className="data shrink-0 text-ink-muted">{requestId}</span> : null}
    </li>
  );
}

function ConnectionDot({ state }: { state: ConnectionState }) {
  const label: Record<ConnectionState, string> = {
    connecting: "Connecting",
    live: "Live",
    retrying: "Reconnecting",
    offline: "Offline",
  };
  // Connection trouble is grey, never amber: amber means a person is needed,
  // and a dropped socket is not a decision.
  const colour =
    state === "live" ? "var(--stable)" : state === "offline" ? "var(--rescue)" : "var(--ink-muted)";

  return (
    <span className="flex items-center gap-1.5 text-12 text-ink-muted">
      <span
        aria-hidden="true"
        className="inline-block h-1.5 w-1.5 rounded-full"
        style={{ background: colour }}
      />
      {label[state]}
    </span>
  );
}
