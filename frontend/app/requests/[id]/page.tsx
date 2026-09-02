"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { use } from "react";
import { api } from "@/lib/api";
import { KIND_LABEL, clockTime, kindColor, statusLabel, urgency, whoLine } from "@/lib/format";

export default function RequestDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const detail = useQuery({
    queryKey: ["request", id],
    queryFn: () => api.requestDetail(id),
  });

  if (detail.isError) {
    return (
      <Shell>
        <p className="text-ink-muted">
          No request with id <span className="data">{id}</span>.
        </p>
      </Shell>
    );
  }
  if (!detail.data) {
    return (
      <Shell>
        <p className="text-ink-muted">Loading…</p>
      </Shell>
    );
  }

  const row = detail.data;
  const breakdown = row.urgency_breakdown;
  const weights = row.urgency_weights;

  return (
    <Shell>
      <div className="mb-6 flex items-baseline gap-3">
        <span
          aria-hidden="true"
          className="inline-block h-5 w-2"
          style={{ background: kindColor(row.kind) }}
        />
        <h1 className="text-24">{row.kind ? KIND_LABEL[row.kind] : "Unread"}</h1>
        <span className="data text-ink-muted">{row.id}</span>
        <span
          className={row.status === "needs_decision" ? "text-signal" : "text-ink-muted"}
        >
          {statusLabel(row.status)}
        </span>
      </div>

      <div className="grid gap-8 lg:grid-cols-2">
        <div>
          <H2>The message</H2>
          <p className="mb-1 whitespace-pre-wrap border border-line p-3 text-ink">
            {row.raw_text}
          </p>
          <p className="mb-6 text-12 text-ink-muted">
            Received {clockTime(row.received_at)} by {row.channel}. Names and phone numbers
            were replaced with pseudonyms before this text was stored or shown to a model.
          </p>

          <H2>Who</H2>
          <p className="mb-6 text-ink">{whoLine(row) || "Not stated in the message."}</p>

          <H2>Where</H2>
          {row.lat !== null && row.lon !== null ? (
            <p className="mb-6">
              <span className="text-ink">{row.location_label}</span>
              <br />
              <span className="data text-ink-muted">
                {row.lat.toFixed(4)}, {row.lon.toFixed(4)} · confidence{" "}
                {row.location_confidence?.toFixed(2)}
              </span>
            </p>
          ) : (
            <p className="mb-6 text-ink-muted">
              Couldn&apos;t place this request. Pick a point on the map or ask the caller for a
              landmark.
            </p>
          )}

          {row.photo_key ? (
            <>
              <H2>Photo</H2>
              <p className="mb-6 text-ink-muted">
                {row.photo_severity === null
                  ? "A photo was attached, but the configured model cannot see images, so it contributed nothing to the score."
                  : `Severity ${row.photo_severity.toFixed(2)} from the photo.`}
              </p>
            </>
          ) : null}
        </div>

        <div>
          <H2>Why it scored {urgency(row.urgency)}</H2>
          <table className="mb-2 w-full border border-line">
            <tbody>
              {(
                [
                  ["Kind of need", breakdown.kind, weights.kind],
                  ["Who is involved", breakdown.vulnerability, weights.vulnerability],
                  ["Photo", breakdown.photo, weights.photo],
                  ["Water level", breakdown.water_level, weights.water_level],
                  ["How recent", breakdown.recency, weights.recency],
                ] as const
              ).map(([label, value, weight]) => (
                <tr key={label} className="border-b border-line last:border-0">
                  <td className="px-3 py-1.5 text-ink-muted">{label}</td>
                  <td className="data px-3 py-1.5 text-right text-ink-muted">
                    ×{(weight ?? 0).toFixed(2)}
                  </td>
                  <td className="data px-3 py-1.5 text-right text-ink">{value.toFixed(3)}</td>
                </tr>
              ))}
              <tr className="border-t border-line">
                <td className="px-3 py-1.5 text-ink">Total</td>
                <td />
                <td className="numeral px-3 py-1.5 text-right text-ink">
                  {breakdown.total.toFixed(3)}
                </td>
              </tr>
            </tbody>
          </table>
          <p className="mb-6 text-12 text-ink-muted">
            This is arithmetic, not a model output. The same message always produces the same
            number.
          </p>

          <H2>What the agent did</H2>
          <ol className="mb-6 flex flex-wrap gap-1.5">
            {row.node_history.map((node, index) => (
              <li
                key={`${node}-${index}`}
                className="border border-line px-2 py-0.5 text-12 text-ink-muted"
              >
                {node}
              </li>
            ))}
          </ol>
          {row.geo_attempts > 1 ? (
            <p className="mb-6 text-12 text-ink-muted">
              The location was retried {row.geo_attempts} times before the agent gave up and
              asked.
            </p>
          ) : null}

          <H2>Trace</H2>
          <div className="overflow-x-auto border border-line">
            <table className="w-full min-w-[420px]">
              <tbody>
                {row.audit.length === 0 ? (
                  <tr>
                    <td className="px-3 py-2 text-ink-muted">Nothing recorded yet.</td>
                  </tr>
                ) : (
                  row.audit.map((event) => (
                    <tr key={event.id} className="border-b border-line/60 last:border-0">
                      <td className="data px-3 py-1 text-ink-muted">{clockTime(event.ts)}</td>
                      <td className="px-3 py-1 text-12 text-ink">
                        {event.tool ?? event.node ?? "—"}
                      </td>
                      <td className="data px-3 py-1 text-right text-ink-muted">
                        {event.latency_ms ? `${event.latency_ms}ms` : ""}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </Shell>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="mx-auto max-w-[110ch] px-6 py-8">
      <nav className="mb-6 text-12">
        <Link href="/" className="text-ink-muted hover:text-ink">
          ← Board
        </Link>
      </nav>
      {children}
    </div>
  );
}

function H2({ children }: { children: React.ReactNode }) {
  return <h2 className="mb-2 text-12 uppercase tracking-wide text-ink-muted">{children}</h2>;
}
