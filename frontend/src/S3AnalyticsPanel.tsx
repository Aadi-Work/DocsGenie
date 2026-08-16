import { useEffect, useMemo, useState } from "react";
import { api, S3Analytics } from "./api";

const PALETTE = ["#1f7a62", "#c56a1d", "#3d6b99", "#8a4a6a", "#6b8f3a", "#b4532a", "#5a6e66", "#2a7f9e"];

function fmtBytes(n: number): string {
  if (!n) return "0 B";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  return `${(n / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

function fmtWhen(iso?: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso.slice(0, 16).replace("T", " ");
  return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function dayLabel(iso: string): string {
  const parts = iso.split("-");
  return parts.length === 3 ? `${Number(parts[1])}/${Number(parts[2])}` : iso;
}

function polar(cx: number, cy: number, r: number, angle: number): [number, number] {
  const rad = ((angle - 90) * Math.PI) / 180;
  return [cx + r * Math.cos(rad), cy + r * Math.sin(rad)];
}

function donutSlice(cx: number, cy: number, inner: number, outer: number, start: number, end: number): string {
  const sweep = Math.min(359.99, Math.max(0, end - start));
  if (sweep <= 0) return "";
  const [ox1, oy1] = polar(cx, cy, outer, start);
  const [ox2, oy2] = polar(cx, cy, outer, start + sweep);
  const [ix2, iy2] = polar(cx, cy, inner, start + sweep);
  const [ix1, iy1] = polar(cx, cy, inner, start);
  const large = sweep > 180 ? 1 : 0;
  return [
    `M ${ox1} ${oy1}`,
    `A ${outer} ${outer} 0 ${large} 1 ${ox2} ${oy2}`,
    `L ${ix2} ${iy2}`,
    `A ${inner} ${inner} 0 ${large} 0 ${ix1} ${iy1}`,
    "Z",
  ].join(" ");
}

type Slice = { label: string; value: number; hint?: string; color?: string };

function PieChart({ slices, center, sub }: { slices: Slice[]; center: string; sub: string }) {
  const colored = slices
    .filter((s) => s.value > 0)
    .map((s, i) => ({ ...s, color: s.color || PALETTE[i % PALETTE.length] }));
  const total = colored.reduce((sum, s) => sum + s.value, 0);
  let cursor = 0;
  const arcs =
    total <= 0
      ? []
      : colored.map((s) => {
          const sweep = (s.value / total) * 360;
          const start = cursor;
          cursor += sweep;
          return { ...s, start, end: start + sweep, pct: (s.value / total) * 100 };
        });

  return (
    <div className="chart-pie-wrap">
      <svg viewBox="0 0 180 180" className="pie-svg" role="img">
        {arcs.length === 0 ? (
          <circle cx="90" cy="90" r="62" fill="#e7eee9" />
        ) : arcs.length === 1 ? (
          <>
            <circle cx="90" cy="90" r="72" fill={arcs[0].color} />
            <circle cx="90" cy="90" r="42" fill="#fbfdfc" />
          </>
        ) : (
          arcs.map((arc) => (
            <path
              key={arc.label}
              d={donutSlice(90, 90, 42, 72, arc.start, arc.end)}
              fill={arc.color}
            >
              <title>{`${arc.label}: ${arc.hint || arc.value} (${arc.pct.toFixed(0)}%)`}</title>
            </path>
          ))
        )}
        <text x="90" y="86" textAnchor="middle" className="pie-center">
          {center}
        </text>
        <text x="90" y="104" textAnchor="middle" className="pie-sub">
          {sub}
        </text>
      </svg>
      <ul className="pie-legend">
        {arcs.map((arc) => (
          <li key={arc.label}>
            <span className="swatch" style={{ background: arc.color }} />
            <span className="pie-legend-label">{arc.label}</span>
            <span className="muted">
              {arc.pct.toFixed(0)}% · {arc.hint || String(arc.value)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function HBarChart({
  rows,
  formatValue,
}: {
  rows: Array<{ label: string; value: number; hint?: string }>;
  formatValue?: (n: number) => string;
}) {
  const max = Math.max(1, ...rows.map((r) => r.value));
  if (!rows.length) return <p className="muted">No data yet.</p>;
  return (
    <ul className="hbar-chart">
      {rows.map((row, i) => {
        const pct = Math.max(2, (row.value / max) * 100);
        return (
          <li key={row.label} title={row.hint || `${row.label}: ${row.value}`}>
            <span className="hbar-label">{row.label}</span>
            <span className="hbar-track">
              <span
                className="hbar-fill"
                style={{ width: `${pct}%`, background: PALETTE[i % PALETTE.length] }}
              />
            </span>
            <span className="hbar-value">{formatValue ? formatValue(row.value) : row.value}</span>
          </li>
        );
      })}
    </ul>
  );
}

function VBarChart({
  days,
}: {
  days: Array<{ day: string; templates: number; generated: number; other: number; count: number }>;
}) {
  const max = Math.max(1, ...days.map((d) => d.count));
  const width = 520;
  const height = 180;
  const padL = 28;
  const padB = 28;
  const padT = 12;
  const plotW = width - padL - 8;
  const plotH = height - padT - padB;
  const gap = plotW / days.length;
  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="vbar-svg" role="img">
      {[0.25, 0.5, 0.75, 1].map((p) => {
        const y = padT + plotH * (1 - p);
        return (
          <g key={p}>
            <line x1={padL} x2={width - 8} y1={y} y2={y} className="chart-grid" />
            <text x={padL - 6} y={y + 3} textAnchor="end" className="chart-axis">
              {Math.round(max * p)}
            </text>
          </g>
        );
      })}
      {days.map((day, i) => {
        const x = padL + i * gap + gap * 0.18;
        const barW = Math.max(4, gap * 0.28);
        const gH = (day.generated / max) * plotH;
        const tH = (day.templates / max) * plotH;
        const base = padT + plotH;
        return (
          <g key={day.day}>
            <rect x={x} y={base - gH} width={barW} height={gH} rx="2" fill="#c56a1d">
              <title>{`${day.day}: ${day.generated} generated`}</title>
            </rect>
            <rect x={x + barW + 2} y={base - tH} width={barW} height={tH} rx="2" fill="#1f7a62">
              <title>{`${day.day}: ${day.templates} templates`}</title>
            </rect>
            <text x={x + barW} y={height - 8} textAnchor="middle" className="chart-axis">
              {dayLabel(day.day)}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

export default function S3AnalyticsPanel() {
  const [data, setData] = useState<S3Analytics | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function load() {
    setBusy(true);
    setError(null);
    try {
      setData(await api.s3Analytics());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load S3 analytics");
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  const typeSlices = useMemo(
    () =>
      (data?.by_kind || []).map((row) => ({
        label: row.label,
        value: row.bytes,
        hint: `${row.count} files · ${fmtBytes(row.bytes)}`,
      })),
    [data],
  );
  const areaSlices = useMemo(
    () =>
      (data?.by_area || []).map((row) => ({
        label: row.label,
        value: row.bytes,
        hint: `${row.count} objects · ${fmtBytes(row.bytes)}`,
      })),
    [data],
  );

  return (
    <section className="card analytics-card">
      <header className="analytics-head">
        <div>
          <h3>S3 analytics</h3>
          <p className="muted">
            {data ? `${data.bucket} · updated ${fmtWhen(data.generated_at)}` : "Inventory of templates and generated files"}
          </p>
        </div>
        <button type="button" className="ghost" onClick={() => void load()} disabled={busy}>
          {busy ? "Refreshing…" : "Refresh"}
        </button>
      </header>

      {error ? <p className="error-copy">{error}</p> : null}
      {!data && busy ? <p className="muted">Reading S3 inventory…</p> : null}

      {data ? (
        <>
          <div className="stat-grid">
            <article className="stat-card">
              <span className="stat-label">Objects</span>
              <strong>{data.totals.objects}</strong>
              <small>{data.totals.office_files} Office files</small>
            </article>
            <article className="stat-card">
              <span className="stat-label">Storage</span>
              <strong>{fmtBytes(data.totals.bytes)}</strong>
              <small>{data.totals.versions} template versions</small>
            </article>
            <article className="stat-card">
              <span className="stat-label">Templates</span>
              <strong>{data.totals.templates}</strong>
              <small>Active guided + S3 files</small>
            </article>
            <article className="stat-card">
              <span className="stat-label">Generated</span>
              <strong>{data.totals.generated_documents}</strong>
              <small>Filled documents in S3</small>
            </article>
          </div>

          <div className="analytics-grid">
            <section className="analytics-pane">
              <h4>Storage by file type</h4>
              <p className="muted pane-hint">Share of S3 bytes</p>
              <PieChart slices={typeSlices} center={fmtBytes(data.totals.bytes)} sub="total" />
            </section>

            <section className="analytics-pane">
              <h4>Storage by area</h4>
              <p className="muted pane-hint">Templates vs generated vs other</p>
              <PieChart slices={areaSlices} center={`${data.totals.objects}`} sub="objects" />
            </section>

            <section className="analytics-pane pane-wide">
              <h4>Most used templates</h4>
              <p className="muted pane-hint">Generated documents + recorded fills</p>
              <HBarChart
                rows={data.most_used.map((row) => ({
                  label: row.name,
                  value: row.score,
                  hint: `${row.generated_count} generated · ${row.usage_count} fills · ${fmtBytes(row.storage_bytes)}`,
                }))}
              />
            </section>

            <section className="analytics-pane pane-wide">
              <h4>Activity · last 14 days</h4>
              <p className="muted pane-hint">Files last modified that day</p>
              <VBarChart days={data.activity} />
              <p className="activity-legend">
                <span className="swatch generated" /> Generated
                <span className="swatch templates" /> Templates
              </p>
            </section>
          </div>

          <div className="analytics-tables">
            <section>
              <h4>Largest files</h4>
              <table className="ver-table">
                <thead>
                  <tr>
                    <th>File</th>
                    <th>Area</th>
                    <th>Size</th>
                    <th>Modified</th>
                  </tr>
                </thead>
                <tbody>
                  {data.largest.map((row) => (
                    <tr key={row.s3_key}>
                      <td title={row.s3_key}>{row.name}</td>
                      <td>{row.area}</td>
                      <td>{fmtBytes(row.size)}</td>
                      <td>{fmtWhen(row.last_modified)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
            <section>
              <h4>Recently updated</h4>
              <table className="ver-table">
                <thead>
                  <tr>
                    <th>File</th>
                    <th>Area</th>
                    <th>Size</th>
                    <th>Modified</th>
                  </tr>
                </thead>
                <tbody>
                  {data.recent.map((row) => (
                    <tr key={row.s3_key}>
                      <td title={row.s3_key}>{row.name}</td>
                      <td>{row.area}</td>
                      <td>{fmtBytes(row.size)}</td>
                      <td>{fmtWhen(row.last_modified)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          </div>
        </>
      ) : null}
    </section>
  );
}
