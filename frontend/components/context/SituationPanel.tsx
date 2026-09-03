"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type {
  GdacsAlert,
  GdacsBlock,
  NdmaBlock,
  ReliefWebBlock,
  RiverBlock,
  SourceBlock,
} from "@/lib/types";

/**
 * Real data about the real flood, beside a synthetic queue.
 *
 * Two rules govern everything on this panel:
 *
 * 1. Nothing here moves an urgency score. The figures are for the coordinator's
 *    judgement, and `test_scoring_imports_no_context_source` keeps it that way.
 * 2. Every source states its own condition. A source that is down prints why it
 *    is down, with a link, rather than disappearing or showing a zero. A blank
 *    where a number should be is indistinguishable from "no damage reported",
 *    and on a relief console those are not the same thing.
 */
export function SituationPanel() {
  const context = useQuery({
    queryKey: ["context"],
    queryFn: () => api.context(),
    // Half-hourly satellite rain is the fastest-moving thing here; nothing
    // benefits from being pulled more often than the sources publish.
    refetchInterval: 15 * 60 * 1000,
  });

  if (context.isLoading) {
    return <Shell>Loading situation data…</Shell>;
  }

  if (context.isError || !context.data) {
    return (
      <Shell>
        <p className="text-ink-muted">
          Couldn&apos;t reach the server for situation data. The queue is unaffected.
        </p>
      </Shell>
    );
  }

  const { river, ndma, reliefweb, gdacs, district, province } = context.data;

  // A block can be missing outright, not merely unavailable: an API older than
  // this bundle simply will not send one. Left unguarded that throws, and one
  // absent field takes the whole panel down -- the opposite of the rule this
  // component is built on, where a source that is down degrades its own tile.
  const missing = (name: string): SourceBlock => ({
    available: false,
    error: `the API did not return a "${name}" block`,
  });

  return (
    <Shell>
      <div className="space-y-4">
        <River block={river ?? (missing("river") as RiverBlock)} />
        <Ndma
          block={ndma ?? (missing("ndma") as NdmaBlock)}
          district={district}
          province={province}
        />
        <Gdacs block={gdacs ?? (missing("gdacs") as GdacsBlock)} />
        <ReliefWeb block={reliefweb ?? (missing("reliefweb") as ReliefWebBlock)} />
        <p className="border-t border-line pt-2 text-12 text-ink-muted">
          Situation data only. None of these figures influence any urgency score,
          and none of them can authorise a dispatch.
        </p>
      </div>
    </Shell>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <section className="border-b border-line p-3 text-12" aria-label="Situation">
      <h2 className="mb-2 text-ink">Situation</h2>
      {children}
    </section>
  );
}

/** The shared failure rendering: name the source, say why, link the evidence. */
function Unavailable({ block, label }: { block: SourceBlock; label: string }) {
  return (
    <p className="text-ink-muted">
      <span className="text-ink">{label}:</span> unavailable —{" "}
      {block.error ?? "no reason given"}
      {block.source_url ? (
        <>
          {" "}
          <a
            href={block.source_url}
            target="_blank"
            rel="noreferrer"
            className="underline hover:text-ink"
          >
            source
          </a>
        </>
      ) : null}
    </p>
  );
}

function River({ block }: { block: RiverBlock }) {
  if (!block.available) return <Unavailable block={block} label="River" />;

  const arrow =
    block.trend === "rising" ? "↑" : block.trend === "falling" ? "↓" : "→";

  return (
    <div>
      <div className="text-ink">
        River discharge {arrow}{" "}
        <span className="tabular-nums">{block.current_m3s}</span> {block.units}
      </div>
      <p className="text-ink-muted">
        {block.trend === "unknown"
          ? "No forecast published for this point."
          : `${block.trend} · peak next 7 days ${block.max_next_7d_m3s ?? "—"} ${block.units}`}
      </p>
      <p className="text-ink-muted">
        {block.model} · {block.as_of}
      </p>
    </div>
  );
}

function Ndma({
  block,
  district,
  province,
}: {
  block: NdmaBlock;
  district: string;
  province: string;
}) {
  if (!block.available) {
    return (
      <div>
        <Unavailable block={{ ...block, source_url: block.report_url ?? block.source_url }} label="NDMA" />
        {block.remedy ? (
          <p className="mt-1 text-ink-muted">{block.remedy}</p>
        ) : null}
      </div>
    );
  }

  return (
    <div>
      <div className="text-ink">
        NDMA Sitrep No. {block.report_number}
        {block.report_date ? ` · ${block.report_date}` : null}
      </div>

      {block.province ? (
        <p className="text-ink-muted">
          {province} cumulative:{" "}
          <span className="tabular-nums">{block.province.houses_total}</span> houses
          damaged, <span className="tabular-nums">{block.province.livestock}</span>{" "}
          livestock lost, <span className="tabular-nums">{block.province.roads_km}</span> km
          roads
        </p>
      ) : (
        <p className="text-ink-muted">No row for {province} in this report.</p>
      )}

      {/* A district absent from the report means no incident was reported in
          the last 24 hours. That is information, and saying nothing would let
          it be misread as a gap in our own data. */}
      <p className="text-ink-muted">
        {district}:{" "}
        {block.district_reported && block.district
          ? `${block.district.houses_total} houses damaged in the last 24 hours`
          : "no incident reported in the last 24 hours"}
      </p>

      {block.report_url ? (
        <a
          href={block.report_url}
          target="_blank"
          rel="noreferrer"
          className="text-ink-muted underline hover:text-ink"
        >
          Source PDF
        </a>
      ) : null}
    </div>
  );
}

/** Alert colours are GDACS's own scheme, not this console's palette. Amber here
 *  would collide with --signal, which means "a human must answer this" and is
 *  never allowed to mean anything else. */
const ALERT_COLOR: Record<string, string> = {
  Red: "var(--rescue)",
  Orange: "var(--medical)",
  Green: "var(--stable)",
};

function AlertDot({ level }: { level: string }) {
  return (
    <span
      aria-hidden
      className="mr-1 inline-block h-2 w-2 rounded-full align-middle"
      style={{ background: ALERT_COLOR[level] ?? "var(--ink-muted)" }}
    />
  );
}

function AlertLine({ alert }: { alert: GdacsAlert }) {
  return (
    <li>
      <AlertDot level={alert.level} />
      <a
        href={alert.url}
        target="_blank"
        rel="noreferrer"
        className="text-ink-muted underline hover:text-ink"
      >
        {alert.level} · {alert.country}
      </a>
      {alert.summary ? (
        <span className="text-ink-muted"> — {alert.summary}</span>
      ) : null}
    </li>
  );
}

function Gdacs({ block }: { block: GdacsBlock }) {
  if (!block.available) return <Unavailable block={block} label="GDACS" />;

  const { Red = 0, Orange = 0, Green = 0 } = block.counts ?? {};
  const here = block.here ?? [];
  const elsewhere = (block.alerts ?? []).filter(
    (a) => !here.some((h) => h.event_id === a.event_id),
  );

  return (
    <div>
      <div className="text-ink">
        Global flood alerts{" "}
        <span className="text-ink-muted">
          ({Red} red, {Orange} orange, {Green} green)
        </span>
      </div>

      {/* The coordinator's own country first. Whether this flood is tracked
          internationally, and at what level, is a different fact from how many
          floods exist worldwide, and it should not have to be hunted for. */}
      {here.length > 0 ? (
        <ul className="mt-1 space-y-1">
          {here.map((alert) => (
            <AlertLine key={alert.event_id} alert={alert} />
          ))}
        </ul>
      ) : (
        <p className="mt-1 text-ink-muted">
          No current GDACS alert for {block.country}. That is an absence of an
          international alert, not an absence of flooding.
        </p>
      )}

      {elsewhere.length > 0 ? (
        <ul className="mt-1 space-y-1">
          {elsewhere.slice(0, 2).map((alert) => (
            <AlertLine key={alert.event_id} alert={alert} />
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function ReliefWeb({ block }: { block: ReliefWebBlock }) {
  if (!block.available) return <Unavailable block={block} label="ReliefWeb" />;

  const reports = block.reports ?? [];
  if (reports.length === 0) {
    return <p className="text-ink-muted">ReliefWeb: no recent reports matched.</p>;
  }

  return (
    <div>
      <div className="text-ink">
        Wider response{" "}
        {/* Which path answered is worth showing: the RSS fallback carries less
            than the JSON API, and a reader should know which they are seeing. */}
        <span className="text-ink-muted">
          ({block.source === "rss" ? "RSS feed" : "ReliefWeb API"})
        </span>
      </div>
      <ul className="mt-1 space-y-1">
        {reports.slice(0, 3).map((report) => (
          <li key={report.url ?? report.title}>
            <a
              href={report.url ?? "#"}
              target="_blank"
              rel="noreferrer"
              className="text-ink-muted underline hover:text-ink"
            >
              {report.title}
            </a>
          </li>
        ))}
      </ul>
    </div>
  );
}
