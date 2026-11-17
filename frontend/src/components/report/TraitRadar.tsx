// Display the reference radar with live scores in canonical RIASEC order.

import type { TraitScore } from "../../api/types";

/** Canonical Holland order. NOT score order — the adjacency is the point. */
const RIASEC = ["realistic", "investigative", "artistic", "social", "enterprising", "conventional"];

const RADIUS = 100;
const RINGS = [0.25, 0.5, 0.75, 1];

interface Props {
  traits: Record<string, TraitScore>;
  code: string[];
}

/** Vertex position. Starts at twelve o'clock and runs clockwise. */
function point(index: number, distance: number): [number, number] {
  const angle = (Math.PI * 2 * index) / RIASEC.length - Math.PI / 2;
  return [160 + Math.cos(angle) * distance, 135 + Math.sin(angle) * distance];
}

function polygon(distances: number[]): string {
  return distances.map((distance, index) => point(index, distance).join(",")).join(" ");
}

export default function TraitRadar({ traits, code }: Props) {
  const order = traits.R ? ["R", "I", "A", "S", "E", "C"] : RIASEC;
  const present = order.filter((trait) => traits[trait] !== undefined);

  // Fewer than three axes can't enclose an area, so the shape would be meaningless.
  if (present.length < 3) {
    return null;
  }

  const scores = order.map((trait) => traits[trait]?.score ?? 0);
  const shape = polygon(scores.map((score) => Math.max(score, 0.02) * RADIUS));

  return (
    <figure className="radar-figure">
      <svg
        viewBox="0 0 320 270"
        className="radar-svg"
        role="img"
        aria-label={order
          .filter((t) => traits[t])
          .map((t) => `${traits[t]!.label} ${Math.round(traits[t]!.score * 100)}%`)
          .join(", ")}
      >
        {/* Recessive scaffolding: rings and spokes sit behind the data and never compete. */}
        {RINGS.map((ring) => (
          <polygon
            key={ring}
            points={polygon(order.map(() => ring * RADIUS))}
            fill="none"
            stroke="var(--color-line)"
            strokeWidth="1"
          />
        ))}
        {order.map((trait, index) => {
          const [x, y] = point(index, RADIUS);
          return (
            <line
              key={trait}
              x1={160}
              y1={135}
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
          fillOpacity="0.25"
          stroke="var(--color-accent)"
          strokeWidth="2"
          strokeLinejoin="round"
        />

        {order.map((trait, index) => {
          const score = traits[trait];

          if (!score) {
            return null;
          }

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

        {order.map((trait, index) => {
          const score = traits[trait];

          if (!score) {
            return null;
          }

          const [x, y] = point(index, RADIUS + 12);
          let textAnchor: "middle" | "start" | "end" = "middle";
          if (index === 1 || index === 2) {
            textAnchor = "start";
          } else if (index === 4 || index === 5) {
            textAnchor = "end";
          }
          const label = RIASEC[index] ?? score.label;
          return (
            <text
              key={trait}
              x={x}
              y={y}
              textAnchor={textAnchor}
              dominantBaseline="middle"
              fill="var(--text-dark)"
              fontSize="10.5"
              fontWeight="700"
            >
              {label.charAt(0).toUpperCase() + label.slice(1)}
            </text>
          );
        })}
      </svg>
    </figure>
  );
}
