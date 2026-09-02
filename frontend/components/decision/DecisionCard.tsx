"use client";

import { useState } from "react";
import type { Decision, DecisionOption } from "@/lib/types";

/**
 * The most important component in the product.
 *
 * Rules it follows, all from hard experience with what people can read under
 * stress:
 *   - The heading is a plain sentence ("One boat, two calls."), never a label
 *     like "Resource Conflict Detected".
 *   - Every option shows the same facts in the same order, so the eye compares
 *     vertically instead of hunting.
 *   - Buttons say what will happen ("Send the boat to A"), not "Approve".
 *   - There is always a third option that dispatches nobody.
 */
export function DecisionCard({
  decision,
  onResolve,
  busy,
}: {
  decision: Decision;
  onResolve: (optionId: string, note?: string) => void;
  busy: boolean;
}) {
  const [note, setNote] = useState("");

  const choices = decision.options.filter((o) => o.is_dispatch || o.facts.Effect !== undefined);
  const compared = decision.options.filter((o) => Object.keys(o.facts).length > 1);
  const factKeys = compared.length
    ? Array.from(new Set(compared.flatMap((o) => Object.keys(o.facts))))
    : [];

  return (
    <section
      aria-label={decision.heading}
      className="flex h-full flex-col gap-3 overflow-y-auto p-4"
    >
      <h2 className="text-18 text-ink">{decision.heading}</h2>

      {compared.length >= 2 ? (
        <div className="grid gap-3 sm:grid-cols-2">
          {compared.map((option) => (
            <OptionFacts key={option.id} option={option} factKeys={factKeys} />
          ))}
        </div>
      ) : null}

      <p className="max-w-[70ch] text-ink-muted">{decision.reasoning}</p>

      <div className="flex flex-wrap items-center gap-2">
        {choices.map((option, index) => (
          <button
            key={option.id}
            type="button"
            disabled={busy}
            onClick={() => onResolve(option.id, note.trim() || undefined)}
            className={[
              "border px-3 py-2 text-15 disabled:opacity-50",
              option.id === decision.recommendation_option_id
                ? "border-signal text-signal"
                : "border-line text-ink hover:bg-surface-2",
            ].join(" ")}
          >
            <span className="data mr-2 text-ink-muted">{index + 1}</span>
            {option.label}
          </button>
        ))}
      </div>

      <label className="flex flex-col gap-1 text-12 text-ink-muted">
        Add a note (optional)
        <input
          value={note}
          onChange={(e) => setNote(e.target.value)}
          disabled={busy}
          maxLength={500}
          className="w-full max-w-[52ch] border border-line bg-depth px-2 py-1.5 text-13 text-ink outline-none focus:border-signal"
        />
      </label>
    </section>
  );
}

function OptionFacts({
  option,
  factKeys,
}: {
  option: DecisionOption;
  factKeys: string[];
}) {
  return (
    <div className="border border-line p-3">
      <div className="mb-2 flex items-baseline gap-2">
        <span className="numeral text-15 text-ink">{option.id}</span>
        {option.request_id ? (
          <span className="data text-ink-muted">{option.request_id}</span>
        ) : null}
      </div>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
        {factKeys.map((key) => (
          <div key={key} className="contents">
            <dt className="text-12 text-ink-muted">{key}</dt>
            <dd
              className={
                key === "Urgency" || key === "Distance" ? "data text-ink" : "text-13 text-ink"
              }
            >
              {option.facts[key] ?? "—"}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
