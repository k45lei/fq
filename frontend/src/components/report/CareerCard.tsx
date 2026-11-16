import { formatPercent } from "../../lib/format";
import type { CareerSuggestion } from "../../api/types";

export default function CareerCard({ career }: { career: CareerSuggestion; familyName?: string }) {
  return (
    <li className="rounded-2xl border border-line bg-surface p-6 shadow-card">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h3 className="text-lg font-bold">{career.name}</h3>
        <span className="rounded-full bg-accent-soft px-3 py-1 text-sm font-bold text-accent">
          {formatPercent(career.fit_score)} fit
        </span>
      </div>
      {career.description && (
        <p className="mt-3 text-sm leading-relaxed text-muted">{career.description}</p>
      )}
      <p className="mt-4 text-sm">
        <span className="font-semibold">Why it fits: </span>
        {career.rationale}
      </p>
      <details className="mt-5 border-t border-line pt-4">
        <summary className="text-sm font-semibold text-accent">Explore this career</summary>
        {career.subjects?.length ? (
          <div className="mt-4">
            <h4 className="text-sm font-bold">Subjects to explore</h4>
            <p className="mt-2 text-sm text-muted">{career.subjects.join(" · ")}</p>
          </div>
        ) : null}
        <h4 className="mt-5 text-sm font-bold">Try this</h4>
        <ul className="mt-2 space-y-2 text-sm text-muted">
          {(career.activities ?? [career.guidance?.project].filter(Boolean)).map((activity) => (
            <li key={activity}>→ {activity}</li>
          ))}
        </ul>
      </details>
    </li>
  );
}
