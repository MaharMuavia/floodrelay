"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { QueueColumn } from "@/components/board/QueueColumn";
import { DecisionDock } from "@/components/decision/DecisionDock";
import { AgentActivity } from "@/components/stream/AgentActivity";
import { api } from "@/lib/api";
import { useAgentStream } from "@/lib/sse";
import type { AgentEvent } from "@/lib/types";

// MapLibre touches `window` on import, so it must not run during SSR.
const ReliefMap = dynamic(
  () => import("@/components/map/ReliefMap").then((m) => m.ReliefMap),
  {
    ssr: false,
    loading: () => (
      <div className="flex h-full items-center justify-center text-ink-muted">
        Loading map…
      </div>
    ),
  },
);

export default function BoardPage() {
  const queryClient = useQueryClient();
  const { events, state } = useAgentStream();

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [activeDecision, setActiveDecision] = useState(0);
  const [dismissed, setDismissed] = useState<string[]>([]);
  const [pulsingIds, setPulsingIds] = useState<Set<string>>(new Set());
  const [scrollTop, setScrollTop] = useState(0);
  const [viewportHeight, setViewportHeight] = useState(600);
  const queueRef = useRef<HTMLDivElement>(null);

  const board = useQuery({ queryKey: ["board"], queryFn: () => api.board() });
  const decisions = useQuery({
    queryKey: ["decisions"],
    queryFn: () => api.decisions(true),
  });
  const heatmap = useQuery({ queryKey: ["heatmap"], queryFn: () => api.heatmap() });
  const demo = useQuery({ queryKey: ["demo"], queryFn: () => api.demoInfo() });

  const rows = useMemo(() => board.data?.requests ?? [], [board.data]);
  const openDecisions = useMemo(
    () => (decisions.data?.decisions ?? []).filter((d) => !dismissed.includes(d.id)),
    [decisions.data, dismissed],
  );

  // Any agent event means the board moved; refetch rather than trying to patch
  // local state from the stream, which would drift.
  const lastRefetch = useRef(0);
  useEffect(() => {
    if (events.length === 0) return;
    const now = Date.now();
    if (now - lastRefetch.current < 700) return;
    lastRefetch.current = now;
    void queryClient.invalidateQueries({ queryKey: ["board"] });
    void queryClient.invalidateQueries({ queryKey: ["decisions"] });
  }, [events, queryClient]);

  // The decision moment: pulse the rows and pins involved, once.
  useEffect(() => {
    const latest = events.find(
      (e: AgentEvent) => e.type === "decision_required",
    ) as Extract<AgentEvent, { type: "decision_required" }> | undefined;
    if (!latest) return;

    setPulsingIds(new Set(latest.request_ids));
    const timer = setTimeout(() => setPulsingIds(new Set()), 2000);
    return () => clearTimeout(timer);
  }, [events]);

  useEffect(() => {
    const measure = () => {
      if (queueRef.current) setViewportHeight(queueRef.current.clientHeight);
    };
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, []);

  const resolve = useMutation({
    mutationFn: ({
      decisionId,
      optionId,
      note,
    }: {
      decisionId: string;
      optionId: string;
      note?: string;
    }) => api.resolveDecision(decisionId, { option_id: optionId, note }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["board"] });
      void queryClient.invalidateQueries({ queryKey: ["decisions"] });
    },
  });

  const openRequest = useCallback((id: string) => {
    window.location.href = `/requests/${id}`;
  }, []);

  // Keyboard: j/k move, Enter opens, 1/2/3 answer the focused card, Esc dismisses.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      if (target && ["INPUT", "TEXTAREA"].includes(target.tagName)) return;

      const active = openDecisions[Math.min(activeDecision, openDecisions.length - 1)];

      if (e.key === "Escape" && active) {
        setDismissed((prev) => [...prev, active.id]);
        return;
      }
      if (active && ["1", "2", "3"].includes(e.key)) {
        const option = active.options[Number(e.key) - 1];
        if (option) {
          e.preventDefault();
          resolve.mutate({ decisionId: active.id, optionId: option.id });
        }
        return;
      }
      if (e.key === "j" || e.key === "k") {
        e.preventDefault();
        const index = rows.findIndex((r) => r.id === selectedId);
        const next =
          e.key === "j"
            ? Math.min(rows.length - 1, index + 1)
            : Math.max(0, index <= 0 ? 0 : index - 1);
        const row = rows[next];
        if (row) setSelectedId(row.id);
        return;
      }
      if (e.key === "Enter" && selectedId) {
        e.preventDefault();
        openRequest(selectedId);
      }
    };

    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [rows, selectedId, openDecisions, activeDecision, resolve, openRequest]);

  const counts = board.data?.counts;
  const pending = openDecisions.length;

  return (
    <div
      className={`flex h-screen flex-col ${pending > 0 ? "decision-active" : ""}`}
    >
      {demo.data?.synthetic ? (
        <div className="shrink-0 border-b border-line bg-surface px-4 py-1 text-12 text-ink-muted">
          Demo data — synthetic requests modelled on published flood reporting. No real people.
        </div>
      ) : null}

      {/* Stacks on a narrow window: side by side, the status line and the nav
          wrap into each other and read as one garbled sentence. */}
      <header className="flex shrink-0 flex-col gap-1 border-b border-line px-4 py-2 sm:flex-row sm:items-baseline sm:justify-between sm:gap-4">
        <div className="flex min-w-0 items-baseline gap-3">
          <h1 className="shrink-0 text-18 text-ink">FloodRelay</h1>
          <span className="min-w-0 text-ink-muted">
            Nowshera district · {counts?.open ?? 0} open ·{" "}
            {pending > 0 ? (
              <span className="font-medium text-signal">{pending} awaiting you</span>
            ) : (
              "nothing awaiting you"
            )}
          </span>
        </div>
        <nav className="flex shrink-0 items-center gap-4 text-12">
          <Link href="/audit" className="text-ink-muted hover:text-ink">
            Audit
          </Link>
          <Link href="/about" className="text-ink-muted hover:text-ink">
            About
          </Link>
        </nav>
      </header>

      {/*
        Three columns at >=1280px, each filling the viewport and scrolling
        internally. Below that the columns stack and the *page* scrolls.

        The explicit heights below the breakpoint are load-bearing: with only
        `min-h-0` the queue collapsed to zero and the map ate the whole screen,
        which silently removed the primary interface on any laptop narrower
        than 1280px. The queue is the thing this console is for, so it keeps
        the most room and stays first in the source order.
      */}
      <main className="grid min-h-0 flex-1 grid-cols-1 overflow-y-auto xl:grid-cols-[minmax(360px,1fr)_minmax(0,1.6fr)_minmax(300px,0.8fr)] xl:overflow-hidden">
        <section
          ref={queueRef}
          className="desaturate min-h-[55vh] border-b border-line xl:min-h-0 xl:border-b-0 xl:border-r"
          aria-label="Triage queue"
        >
          {board.isError ? (
            <p className="p-4 text-ink-muted">
              Couldn&apos;t reach the server. The queue will fill in when the connection returns.
            </p>
          ) : (
            <QueueColumn
              rows={rows}
              selectedId={selectedId}
              pulsingIds={pulsingIds}
              onSelect={setSelectedId}
              onOpen={openRequest}
              scrollTop={scrollTop}
              onScroll={setScrollTop}
              viewportHeight={viewportHeight}
            />
          )}
        </section>

        <section
          className="desaturate min-h-[340px] border-b border-line xl:min-h-0 xl:border-b-0 xl:border-r"
          aria-label="Map"
        >
          <ReliefMap
            rows={rows}
            resources={board.data?.resources ?? []}
            heatmap={heatmap.data?.cells ?? []}
            selectedId={selectedId}
            pulsingIds={pulsingIds}
            onSelect={setSelectedId}
          />
        </section>

        <section className="desaturate min-h-[260px] xl:min-h-0" aria-label="Agent activity">
          <AgentActivity events={events} state={state} />
        </section>
      </main>

      <footer className="shrink-0">
        <DecisionDock
          decisions={openDecisions}
          activeIndex={activeDecision}
          busy={resolve.isPending}
          onCycle={() => setActiveDecision((i) => (i + 1) % Math.max(1, openDecisions.length))}
          onResolve={(decisionId, optionId, note) =>
            resolve.mutate({ decisionId, optionId, note })
          }
        />
      </footer>
    </div>
  );
}
