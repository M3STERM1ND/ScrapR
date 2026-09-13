import type { Source, VisualizationSpec } from "@/lib/api/client";

/**
 * Charts and tables, as inline SVG and markup (`DEC-11`, `REQ-VIZ-001..006`).
 *
 * **The renderer is deliberately dumb.** It draws what the spec says and makes
 * no decisions — which form, which points, which unit were all settled in the
 * pipeline. That is what lets a Phase 7 export renderer be a second dumb
 * reader of the same spec and produce the same picture (`REQ-EXP-005 AC-2`
 * forbids re-deriving anything at export time).
 *
 * **No charting library**, per `DEC-11 §2`: SVG is markup, so a browser and a
 * headless PDF pipeline render it identically because there is nothing for
 * them to disagree about.
 *
 * **One ochre series at most** (`DEC-11 §5`). Series identity travels on
 * position and shape before colour, for the same reason claim type and
 * confidence do: a reader who cannot tell two lines apart in greyscale cannot
 * read the chart in a printed export either.
 */

type Props = {
  viz: VisualizationSpec;
  sourceForEvidence: Map<string, Source>;
};

type Point = { label: string; value: string; evidence_id: string };
type Series = { name: string; points: Point[] };

function readSpec(spec: Record<string, unknown>): {
  title: string;
  unit: string | null;
  series: Series[];
} {
  return {
    title: typeof spec.title === "string" ? spec.title : "",
    unit: typeof spec.unit === "string" ? spec.unit : null,
    series: Array.isArray(spec.series) ? (spec.series as Series[]) : [],
  };
}

export function Visualization({ viz, sourceForEvidence }: Props) {
  const { title, unit, series } = readSpec(viz.spec as Record<string, unknown>);
  const points = series[0]?.points ?? [];

  // `REQ-VIZ-002 AC-3`: no placeholder is ever rendered as if it were real. A
  // chart frame with no data is a placeholder with axes.
  if (points.length === 0) return null;

  return (
    <figure className="viz mt-8">
      <figcaption className="claim-label">
        {title}
        {unit ? ` (${unit})` : ""}
      </figcaption>

      <div className="mt-3">
        {viz.kind === "line" ? <LineChart points={points} /> : null}
        {viz.kind === "bar" ? <BarChart points={points} /> : null}
        {viz.kind === "metric" ? <Metric point={points[0]} unit={unit} /> : null}
        {viz.kind === "comparison" || viz.kind === "table" || viz.kind === "matrix" ? (
          <DataTable points={points} unit={unit} />
        ) : null}
      </div>

      <Provenance viz={viz} sourceForEvidence={sourceForEvidence} />
    </figure>
  );
}

/* ---- forms ------------------------------------------------------------ */

const WIDTH = 560;
const HEIGHT = 200;
const PAD = 32;

function scale(points: Point[]) {
  const values = points.map((point) => Number(point.value));
  const max = Math.max(...values, 0);
  const min = Math.min(...values, 0);
  const span = max - min || 1;
  return {
    x: (index: number) =>
      PAD + (index * (WIDTH - PAD * 2)) / Math.max(points.length - 1, 1),
    y: (value: number) =>
      HEIGHT - PAD - ((value - min) / span) * (HEIGHT - PAD * 2),
  };
}

function LineChart({ points }: { points: Point[] }) {
  const { x, y } = scale(points);
  const path = points
    .map((point, index) => `${index === 0 ? "M" : "L"} ${x(index)} ${y(Number(point.value))}`)
    .join(" ");

  return (
    <svg
      viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
      className="w-full"
      role="img"
      aria-label={`Line chart: ${points.map((p) => `${p.label} ${p.value}`).join(", ")}`}
    >
      <line
        x1={PAD}
        y1={HEIGHT - PAD}
        x2={WIDTH - PAD}
        y2={HEIGHT - PAD}
        stroke="var(--color-line-strong)"
        strokeWidth="1"
      />
      <path d={path} fill="none" stroke="var(--color-ochre)" strokeWidth="1.5" />
      {points.map((point, index) => (
        <circle
          key={point.evidence_id}
          cx={x(index)}
          cy={y(Number(point.value))}
          r="3"
          fill="var(--color-ochre)"
        />
      ))}
      {points.map((point, index) => (
        <text
          key={`${point.evidence_id}-label`}
          x={x(index)}
          y={HEIGHT - PAD + 16}
          textAnchor="middle"
          fontSize="11"
          fill="var(--color-ink-muted)"
        >
          {point.label}
        </text>
      ))}
    </svg>
  );
}

function BarChart({ points }: { points: Point[] }) {
  const { y } = scale(points);
  const slot = (WIDTH - PAD * 2) / points.length;
  const barWidth = Math.min(slot * 0.6, 48);

  return (
    <svg
      viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
      className="w-full"
      role="img"
      aria-label={`Bar chart: ${points.map((p) => `${p.label} ${p.value}`).join(", ")}`}
    >
      <line
        x1={PAD}
        y1={HEIGHT - PAD}
        x2={WIDTH - PAD}
        y2={HEIGHT - PAD}
        stroke="var(--color-line-strong)"
        strokeWidth="1"
      />
      {points.map((point, index) => {
        const top = y(Number(point.value));
        const left = PAD + index * slot + (slot - barWidth) / 2;
        return (
          <g key={point.evidence_id}>
            <rect
              x={left}
              y={top}
              width={barWidth}
              height={HEIGHT - PAD - top}
              fill="var(--color-ochre)"
            />
            <text
              x={left + barWidth / 2}
              y={HEIGHT - PAD + 16}
              textAnchor="middle"
              fontSize="11"
              fill="var(--color-ink-muted)"
            >
              {point.label}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

function Metric({ point, unit }: { point: Point; unit: string | null }) {
  return (
    <p className="display-m text-ink tnum">
      {point.value}
      {unit ? <span className="text-micro text-ink-muted"> {unit}</span> : null}
    </p>
  );
}

/**
 * `REQ-VIZ-006`: tables are first-class, not a fallback.
 *
 * `AC-2` wants them readable at narrow widths, which is what the horizontal
 * scroll container is for — a table that reflows into unreadable columns is
 * worse than one the reader can push sideways.
 */
function DataTable({ points, unit }: { points: Point[]; unit: string | null }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-micro">
        <thead>
          <tr>
            <th scope="col" className="py-2 text-left text-ink-muted">
              Period
            </th>
            <th scope="col" className="py-2 text-right text-ink-muted">
              Value{unit ? ` (${unit})` : ""}
            </th>
          </tr>
        </thead>
        <tbody>
          {points.map((point) => (
            <tr key={point.evidence_id} className="border-t border-line">
              <td className="py-2 text-ink-soft">{point.label}</td>
              <td className="py-2 text-right text-ink tnum">{point.value}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * `REQ-VIZ-004`: a chart exposes the sources of its data, and all of them when
 * it combines several.
 *
 * Under the chart rather than in a tooltip, because an exported PDF has no
 * pointer and `REQ-EXP-009` requires citations survive export.
 */
function Provenance({
  viz,
  sourceForEvidence,
}: {
  viz: VisualizationSpec;
  sourceForEvidence: Map<string, Source>;
}) {
  const names = [
    ...new Set(
      viz.evidence_ids
        .map((id) => sourceForEvidence.get(id)?.name)
        .filter((name): name is string => Boolean(name)),
    ),
  ];

  if (names.length === 0) return null;

  return (
    <p className="mt-3 text-micro text-ink-muted">
      Data from {names.join(", ")}
    </p>
  );
}
