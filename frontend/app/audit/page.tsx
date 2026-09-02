"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { api } from "@/lib/api";
import { clockTime } from "@/lib/format";

export default function AuditPage() {
  const audit = useQuery({ queryKey: ["audit"], queryFn: () => api.audit() });
  const events = audit.data?.events ?? [];

  return (
    <div className="px-6 py-8">
      <nav className="mb-6 text-12">
        <Link href="/" className="text-ink-muted hover:text-ink">
          ← Board
        </Link>
      </nav>

      <div className="mb-4 flex items-baseline gap-3">
        <h1 className="text-24">Audit trail</h1>
        <span className="text-ink-muted">
          {audit.data?.note ?? "Append-only. Entries are never edited or removed."}
        </span>
      </div>

      {events.length === 0 ? (
        <p className="text-ink-muted">
          Nothing recorded yet. Every tool call and model call appears here as the agent works.
        </p>
      ) : (
        <div className="overflow-x-auto border border-line">
          <table className="w-full min-w-[900px] border-collapse">
            <thead>
              <tr className="border-b border-line text-left text-12 text-ink-muted">
                <th className="px-3 py-2 font-medium">Time</th>
                <th className="px-3 py-2 font-medium">Actor</th>
                <th className="px-3 py-2 font-medium">Node</th>
                <th className="px-3 py-2 font-medium">Tool</th>
                <th className="px-3 py-2 font-medium">Request</th>
                <th className="px-3 py-2 font-medium">Output</th>
                <th className="px-3 py-2 text-right font-medium">ms</th>
                <th className="px-3 py-2 text-right font-medium">Tokens</th>
              </tr>
            </thead>
            <tbody>
              {events.map((event) => (
                <tr
                  key={event.id}
                  className="border-b border-line/60 align-top last:border-0 hover:bg-surface-2"
                >
                  <td className="data whitespace-nowrap px-3 py-1.5 text-ink-muted">
                    {clockTime(event.ts)}
                  </td>
                  <td className="px-3 py-1.5 text-12">
                    <span
                      className={
                        event.actor === "coordinator" ? "text-signal" : "text-ink-muted"
                      }
                    >
                      {event.actor}
                    </span>
                  </td>
                  <td className="px-3 py-1.5 text-12 text-ink">{event.node ?? "—"}</td>
                  <td className="px-3 py-1.5 text-12 text-ink">{event.tool ?? "—"}</td>
                  <td className="data px-3 py-1.5 text-ink-muted">
                    {event.request_id ?? "—"}
                  </td>
                  <td className="max-w-[38ch] truncate px-3 py-1.5 text-12 text-ink-muted">
                    {event.error ? (
                      <span className="text-rescue">{event.error}</span>
                    ) : (
                      event.output_digest || "—"
                    )}
                  </td>
                  <td className="data px-3 py-1.5 text-right text-ink-muted">
                    {event.latency_ms ?? "—"}
                  </td>
                  <td className="data px-3 py-1.5 text-right text-ink-muted">
                    {event.tokens ?? "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
