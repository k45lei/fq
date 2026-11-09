/**
 * The six trait scores as a hexagon.
 *
 * A radar is usually the wrong call — area scales with the square of the radius, so it
 * exaggerates differences, and axis order changes the shape arbitrarily. Neither objection
 * applies here, for one domain reason: **RIASEC is canonically a hexagon**, and the order is
 * not arbitrary. Adjacent types on Holland's hexagon are genuinely more similar than opposite
 * ones, so the shape carries real meaning — a profile leaning to one side reads differently
 * from one spread across opposites.
 *
 * It is the gestalt, not the reading. Every vertex is labelled with its value, and the bars
 * beside it are what you actually compare magnitudes with.
 */

import type { TraitScore } from "../../api/types";

/** Canonical Holland order. NOT score order — the adjacency is the point. */
const RIASEC = ["realistic", "investigative", "artistic", "social", "enterprising", "conventional"];

const SIZE = 260;
const CENTRE = SIZE / 2;
const RADIUS = 88;
const RINGS = [0.25, 0.5, 0.75, 1];

interface Props {
  traits: Record<string, TraitScore>;
  code: string[];
}

/** Vertex position. Starts at twelve o'clock and runs clockwise. */
function point(index: number, distance: number): [number, number] {
  const angle = (Math.PI * 2 * index) / RIASEC.length - Math.PI / 2;
  return [CENTRE + Math.cos(angle) * distance, CENTRE + Math.sin(angle) * distance];
}

function polygon(distances: number[]): string {
  return distances.map((distance, index) => point(index, distance).join(",")).join(" ");
}

export default function TraitRadar({ traits, code }: Props) {
  const present = RIASEC.filter((trait) => traits[trait] !== undefined);

  // Fewer than three axes can't enclose an area, so the shape would be meaningless.
  if (present.length < 3) return null;

  const scores = RIASEC.map((trait) => traits[trait]?.score ?? 0);
  const shape = polygon(scores.map((score) => Math.max(score, 0.02) * RADIUS));

  return (
    <figure className="flex flex-col items-center">
      <svg
        viewBox={`0 0 ${SIZE} ${SIZE}`}
        className="w-full max-w-[280px]"
        role="img"
        aria-label={RIASEC.filter((t) => traits[t])
          .map((t) => `${traits[t]!.label} ${Math.round(traits[t]!.score * 100)}%`)
          .join(", ")}
      >
        {/* Recessive scaffolding: rings and spokes sit behind the data and never compete. */}
        {RINGS.map((ring) => (
          <polygon
            key={ring}
            points={polygon(RIASEC.map(() => ring * RADIUS))}
            fill="none"
            stroke="var(--color-line)"
            strokeWidth="1"
          />
        ))}
        {RIASEC.map((trait, index) => {
          const [x, y] = point(index, RADIUS);
          return (
            <line
              key={trait}
              x1={CENTRE}
              y1={CENTRE}
              x2={x}
              y2={y}
              stroke="var(--color-line)"
              strokeWidth="1"
            />
          );
        })}

        <polygon
          points={shape}
          fill="var(--color-accent)"
          fillOpacity="0.16"
          stroke="var(--color-accent)"
          strokeWidth="2"
          strokeLinejoin="round"
        />

        {RIASEC.map((trait, index) => {
          const score = traits[trait];
          if (!score) return null;
          const [x, y] = point(index, Math.max(score.score, 0.02) * RADIUS);
          const inCode = code.includes(trait);
          return (
            <circle
              key={trait}
              cx={x}
              cy={y}
              r={inCode ? 4.5 : 3}
              fill="var(--color-accent)"
              // A surface ring keeps a marker legible where it sits on the outline.
              stroke="var(--color-surface)"
              strokeWidth="2"
            />
          );
        })}

        {RIASEC.map((trait, index) => {
          const score = traits[trait];
          if (!score) return null;
          const [x, y] = point(index, RADIUS + 26);
          return (
            <text
              key={trait}
              x={x}
              y={y}
              textAnchor="middle"
              dominantBaseline="middle"
              className="fill-muted text-[11px]"
            >
              {score.label}
            </text>
          );
        })}
      </svg>

      <figcaption className="mt-2 text-center text-xs text-muted">
        Traits in their standard order — neighbouring ones are the most alike.
      </figcaption>
    </figure>
  );
}
