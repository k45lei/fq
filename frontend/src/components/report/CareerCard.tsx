/**
 * One suggested career, with what to do about it.
 *
 * The fit score is a single value, so it's a number with a thin meter rather than a chart —
 * the meter makes cards scannable against each other, the number carries the reading.
 *
 * Pay and guidance both render only when present. There is deliberately no "unavailable"
 * placeholder for either: the absence is normal, and drawing attention to it would imply
 * something is missing rather than simply not claimed.
 */

import { formatPay, formatPercent } from "../../lib/format";
import type { CareerSuggestion } from "../../api/types";

interface Props {
  career: CareerSuggestion;
  familyName: string | undefined;
}

const GUIDANCE_LABELS = {
  study: "Study",
  project: "Try",
  topic: "Learn",
} as const;

export default function CareerCard({ career, familyName }: Props) {
  const pay = career.pay ? formatPay(career.pay) : null;

  // flatMap rather than map+filter: it narrows `detail` to a string without a type predicate.
  const steps = (["study", "project", "topic"] as const).flatMap((key) => {
    const detail = career.guidance?.[key];
    return detail ? [{ key, label: GUIDANCE_LABELS[key], detail }] : [];
  });

  return (
    <li className="flex flex-col rounded-2xl border border-line bg-surface p-5 shadow-card">
      <div className="flex items-baseline justify-between gap-4">
        <h3 className="font-medium">{career.name}</h3>
        <span className="shrink-0 text-sm tabular-nums text-muted">
          {formatPercent(career.fit_score)}
        </span>
      </div>

      <div className="mt-2.5 h-1 w-full overflow-hidden rounded-full bg-line">
        <div
          className="h-full rounded-full bg-accent"
          style={{ width: `${Math.max(career.fit_score * 100, 2)}%` }}
        />
      </div>

      {familyName && (
        <p className="mt-3 text-xs tracking-wide text-muted uppercase">{familyName}</p>
      )}
      <p className="mt-2 text-sm leading-relaxed">{career.rationale}</p>

      {steps.length > 0 && (
        <dl className="mt-4 space-y-2.5 border-t border-line pt-4">
          {steps.map((step) => (
            <div key={step.key} className="flex gap-3">
              <dt className="w-14 shrink-0 text-xs font-medium tracking-wide text-accent uppercase">
                {step.label}
              </dt>
              <dd className="flex-1 text-sm leading-relaxed text-muted text-pretty">
                {step.detail}
              </dd>
            </div>
          ))}
        </dl>
      )}

      {pay && (
        <p className="mt-4 border-t border-line pt-4 text-sm text-muted">
          Typical pay: <span className="tabular-nums text-ink">{pay}</span>
          {/* A placeholder figure says so on the spot. Burying the caveat in a footnote would
              leave the number reading as sourced for anyone who skims. */}
          {career.pay?.source === "mock" && " · indicative only"}
          {career.pay?.source_url && (
            <>
              {" · "}
              <a
                href={career.pay.source_url}
                target="_blank"
                rel="noreferrer"
                className="underline underline-offset-2"
              >
                source
              </a>
            </>
          )}
        </p>
      )}
    </li>
  );
}
