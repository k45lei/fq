/**
 * Six trait scores as horizontal bars.
 *
 * Form: magnitude across named categories with long labels, so horizontal bars — not a radar
 * or a pie, both of which make six values harder to compare, not easier.
 *
 * Colour deliberately encodes nothing. This is one measure across six categories, so length is
 * the encoding and every bar takes the same hue; a six-colour palette here would be decoration
 * pretending to be information. The top three — the ones forming the Holland code — are
 * distinguished by weight and an explanatory line rather than by colour alone, so the
 * distinction survives greyscale and screen readers.
 *
 * Each bar is directly labelled with its trait and percentage, which is why there's no axis,
 * no gridlines and no legend: a single series names itself.
 */

import { formatPercent } from "../../lib/format";
import type { TraitScore } from "../../api/types";

interface Props {
  traits: Record<string, TraitScore>;
  code: string[];
}

export default function TraitBars({ traits, code }: Props) {
  // Insertion order is the server's ranking, highest first — including its tie-break, which
  // re-sorting here would lose.
  const entries = Object.entries(traits);

  return (
    <ul className="space-y-4">
      {entries.map(([trait, score]) => {
        const inCode = code.includes(trait);

        return (
          <li key={trait}>
            <div className="flex items-baseline justify-between gap-4">
              <span className={inCode ? "font-medium" : "text-muted"}>{score.label}</span>
              <span className="text-sm tabular-nums text-muted">
                {formatPercent(score.score)}
              </span>
            </div>

            <div className="mt-1.5 h-2 w-full overflow-hidden rounded-full bg-line">
              <div
                className={`h-full rounded-full transition-[width] duration-500 ${
                  inCode ? "bg-accent" : "bg-accent-weak"
                }`}
                style={{ width: `${Math.max(score.score * 100, 1.5)}%` }}
              />
            </div>

            {inCode && <p className="mt-2 text-sm text-muted">{score.blurb}</p>}
          </li>
        );
      })}
    </ul>
  );
}
