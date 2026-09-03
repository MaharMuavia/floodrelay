"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { api } from "@/lib/api";

/**
 * What is autonomous, what is not, and where the data comes from.
 *
 * This page exists because a console that makes decisions about people should
 * be able to state plainly which decisions it makes alone.
 */
export default function AboutPage() {
  const health = useQuery({ queryKey: ["health"], queryFn: () => api.health() });
  const demo = useQuery({ queryKey: ["demo"], queryFn: () => api.demoInfo() });
  const formula = useQuery({ queryKey: ["formula"], queryFn: () => api.formula() });

  const checks = (health.data?.checks ?? {}) as Record<string, unknown>;
  const photo = String(checks.photo_severity ?? "");
  const visionOff = photo.startsWith("unavailable");
  const toolCallingOn = health.data?.models.tool_calling === "active";
  const toolCallingDetail = health.data?.models.tool_calling_detail ?? "";

  return (
    <div className="mx-auto max-w-[80ch] px-6 py-8">
      <nav className="mb-6 text-12">
        <Link href="/" className="text-ink-muted hover:text-ink">
          ← Board
        </Link>
      </nav>

      <h1 className="mb-6 text-24">About this console</h1>

      <Section title="What the agent does on its own">
        <ul className="list-disc space-y-1 pl-5 text-ink-muted">
          <li>Reads each message and extracts what is needed, in English, Urdu or Roman Urdu.</li>
          <li>Resolves the location, preferring coordinates the caller sent over a place name.</li>
          <li>Scores urgency with a fixed formula, then explains that score in words.</li>
          <li>Merges an obvious repeat of a request it has already seen.</li>
          <li>Matches shelter and food requests to the nearest capable resource.</li>
        </ul>
      </Section>

      <Section title="What it always stops and asks about">
        <ul className="list-disc space-y-1 pl-5 text-ink-muted">
          <li>Any rescue or medical call, whatever its confidence.</li>
          <li>A location it could not place, after one retry.</li>
          <li>Two open requests that want the same resource.</li>
          <li>A possible duplicate it is not sure about.</li>
        </ul>
        <p className="mt-3 text-ink">
          Nothing reaches a responder without a person approving it. That rule is enforced in
          code by a hook on every tool call, not by an instruction in a prompt, and the test
          that proves an unapproved dispatch raises is the most important test in the
          repository.
        </p>
      </Section>

      <Section title="How urgency is computed">
        <p className="mb-2 text-ink-muted">
          {formula.data?.note ??
            "Urgency is computed by a fixed formula, never by the language model."}
        </p>
        {formula.data ? (
          <table className="w-full max-w-[40ch] border border-line">
            <tbody>
              {Object.entries(formula.data.weights).map(([key, weight]) => (
                <tr key={key} className="border-b border-line last:border-0">
                  <td className="px-2 py-1 capitalize text-ink-muted">
                    {key.replace(/_/g, " ")}
                  </td>
                  <td className="data px-2 py-1 text-right text-ink">
                    {weight.toFixed(2)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
      </Section>

      <Section title="What is not built">
        <ul className="list-disc space-y-1 pl-5 text-ink-muted">
          <li>No sign-in and no multi-tenancy. Anyone who can reach the console can use it.</li>
          <li>
            The WhatsApp webhook verifies its signature and refuses anything unsigned, but the
            payload shape has never been checked against a real WhatsApp Business account. It
            is off entirely unless a secret is configured.
          </li>
          {visionOff ? (
            <li className="text-ink">
              Photo severity is switched off: {photo.replace("unavailable: ", "")}. Requests
              with photos are scored without the photo term rather than with a guessed one.
            </li>
          ) : null}
          <li>No mobile app, no offline sync, and the console itself is English only.</li>
        </ul>
      </Section>

      <Section title="Where the data comes from">
        <p className="text-ink">
          The requests are synthetic. The situation around them is real.
        </p>
        <p className="mt-2 text-ink-muted">
          {demo.data?.note ??
            "Synthetic requests modelled on published flood reporting. No real people."}{" "}
          No help request shown here was sent by anybody.
        </p>
        <p className="mt-2 text-ink-muted">
          Everything else on the console is live. Places are resolved with
          OpenStreetMap&apos;s Nominatim and rendered on OpenStreetMap tiles; rainfall
          and river discharge come from Open-Meteo; satellite layers come from NASA
          GIBS; national damage figures are parsed from NDMA&apos;s daily situation
          report; wider-response headlines come from ReliefWeb. All are free services
          used within their usage policies, and results are cached so the console does
          not re-ask.
        </p>

        <h3 className="mb-1 mt-4 text-ink">What the satellite layers can and cannot show</h3>
        <ul className="list-disc space-y-1 pl-5 text-ink-muted">
          <li>
            <span className="text-ink">Cloud beats optical sensors.</span> MODIS and VIIRS
            cannot see the ground through cloud, and a flood happens under cloud. Read the
            key: <span className="text-ink">grey</span> is &ldquo;Insufficient Data&rdquo;,
            which almost always means cloud, while <span className="text-ink">clear</span>{" "}
            means &ldquo;No Water&rdquo;. Over this district grey routinely covers more
            than half a tile and every water class together covers under one percent, so
            reading grey as coverage inverts the picture entirely.
          </li>
          <li>
            <span className="text-ink">250 m pixels describe areas, not households.</span>{" "}
            A flood pixel over a village is not evidence about any one roof, and nothing on
            this console uses it that way — the same reason photo severity is switched off
            rather than guessed.
          </li>
          <li>
            Radar (Sentinel-1) does see through cloud, but its coverage is scene-based and
            is frequently absent over any given district on any given day. It is offered
            best-effort and says so when there is nothing to show.
          </li>
        </ul>

        <h3 className="mb-1 mt-4 text-ink">What this data is not allowed to do</h3>
        <p className="text-ink-muted">
          None of it touches the urgency formula above, and none of it can authorise a
          dispatch. Urgency stays computable from the message alone, which is what makes
          it explainable to the person being asked to act on it. That is enforced by a
          test that reads this repository&apos;s own imports, not by a convention.
        </p>
      </Section>

      <Section title="Running configuration">
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1">
          <Row label="Store" value={health.data?.store ?? "…"} />
          <Row label="Model provider" value={health.data?.models.provider ?? "…"} />
          <Row label="Reasoning model" value={health.data?.models.heavy ?? "…"} />
          <Row label="Extraction model" value={health.data?.models.light ?? "…"} />
          <Row label="Tool calling" value={health.data?.models.tool_calling ?? "…"} />
          <Row label="Photo severity" value={photo || "…"} />
          <Row label="Tracing" value={String(checks.tracing ?? "…")} />
        </dl>
        <p className="mt-3 text-ink-muted">
          {toolCallingOn
            ? "The agent chooses its own tools: it decides when to geocode, when to check rainfall or river level, and what to look up before explaining a score. Every one of those calls goes through the same hook that refuses a dispatch without your approval."
            : toolCallingDetail ||
              "The configured model cannot call tools, so the pipeline calls them from Python around it."}
        </p>
      </Section>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mb-8">
      <h2 className="mb-2 text-18 text-ink">{title}</h2>
      {children}
    </section>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <>
      <dt className="text-ink-muted">{label}</dt>
      <dd className="data text-ink">{value}</dd>
    </>
  );
}
