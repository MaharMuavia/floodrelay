/**
 * SSE with reconnect and backoff.
 *
 * The coordinator is on bad wifi at 2am. A feed that silently dies and shows
 * stale rows is worse than one that says "reconnecting", so connection state is
 * surfaced in the header rather than hidden.
 */

"use client";

import { useEffect, useRef, useState } from "react";
import { API_BASE } from "./api";
import type { AgentEvent, ConnectionState } from "./types";

const MAX_BUFFER = 200;
const BACKOFF_MS = [1_000, 2_000, 4_000, 8_000, 15_000, 30_000];

export function useAgentStream(enabled = true) {
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [state, setState] = useState<ConnectionState>("connecting");
  const [lastEventAt, setLastEventAt] = useState<number | null>(null);

  const attemptRef = useRef(0);
  const sourceRef = useRef<EventSource | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;

    const connect = () => {
      if (cancelled) return;
      sourceRef.current?.close();

      const source = new EventSource(`${API_BASE}/stream?replay=30`);
      sourceRef.current = source;

      source.onopen = () => {
        if (cancelled) return;
        attemptRef.current = 0;
        setState("live");
      };

      source.onmessage = (raw: MessageEvent<string>) => {
        if (cancelled) return;
        try {
          const parsed = JSON.parse(raw.data) as AgentEvent;
          setLastEventAt(Date.now());
          // Heartbeats prove the link is alive but are not activity.
          if (parsed.type === "heartbeat") return;
          setEvents((prev) => [parsed, ...prev].slice(0, MAX_BUFFER));
        } catch {
          /* a malformed frame is not worth tearing the stream down for */
        }
      };

      source.onerror = () => {
        if (cancelled) return;
        source.close();
        const attempt = Math.min(attemptRef.current, BACKOFF_MS.length - 1);
        const delay = BACKOFF_MS[attempt] ?? 30_000;
        attemptRef.current += 1;
        setState(attemptRef.current > 5 ? "offline" : "retrying");
        timerRef.current = setTimeout(connect, delay);
      };
    };

    connect();

    return () => {
      cancelled = true;
      if (timerRef.current) clearTimeout(timerRef.current);
      sourceRef.current?.close();
    };
  }, [enabled]);

  return { events, state, lastEventAt };
}

/** The named SSE event types the server sends, for filtering the activity feed. */
export function isActivity(event: AgentEvent): boolean {
  return (
    event.type === "node_start" ||
    event.type === "tool_call" ||
    event.type === "node_complete" ||
    event.type === "request_received" ||
    event.type === "decision_required" ||
    event.type === "decision_resolved"
  );
}
