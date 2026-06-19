import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { PlotlyChart } from "./PlotlyChart";

export type ChartType = "table" | "bar" | "line" | "pie" | "scatter" | "histogram" | "box" | "heatmap";

export interface ChartSpec {
  type?: ChartType;
  x?: string;
  y?: string | string[];
  series?: string | string[];
  title?: string;
  reason?: string;
}

interface DynamicChartProps {
  chartSpec?: ChartSpec;
  rows: Record<string, unknown>[];
  typeOverride?: ChartType;
  visualizationDsl?: Record<string, unknown>;
}

const COLORS = ["#6366f1", "#06b6d4", "#10b981", "#f59e0b", "#f43f5e", "#a855f7", "#14b8a6", "#eab308"];

export function DynamicChart({ chartSpec, rows, typeOverride, visualizationDsl }: DynamicChartProps) {
  if (visualizationDsl && typeOverride !== "table") {
    return (
      <PlotlyChart
        chartSpec={chartSpec}
        rows={rows}
        typeOverride={typeOverride}
        visualizationDsl={visualizationDsl}
      />
    );
  }

  const columns = Object.keys(rows[0] || {});
  const numericColumns = columns.filter((column) => rows.some((row) => isNumeric(row[column])));
  const labelColumns = columns.filter((column) => !numericColumns.includes(column));
  const xKey = chartSpec?.x && columns.includes(chartSpec.x) ? chartSpec.x : labelColumns[0] || columns[0];
  const inferredType = inferType(columns, numericColumns, chartSpec?.type);
  const type = typeOverride || inferredType;
  const requestedY = Array.isArray(chartSpec?.y) ? chartSpec.y : chartSpec?.y ? [chartSpec.y] : [];
  const validRequestedY = requestedY.filter((key) => columns.includes(key));
  const yKeys = requestedY.length > 0
    ? validRequestedY.slice(0, type === "pie" ? 1 : 4)
    : numericColumns.slice(0, type === "pie" ? 1 : 4);
  const yKey = yKeys[0];

  if (type === "table") return null;
  const minimumRows = type === "line" || type === "scatter" || type === "box" ? 2 : 1;
  if (rows.length < minimumRows || !xKey || !yKey) {
    return (
      <EmptyChart
        title={chartSpec?.title || "Chart"}
        message={chartSpec?.reason || "Not enough compatible rows or columns to render this chart."}
      />
    );
  }

  const seriesKeys = seriesColumns(chartSpec?.series, columns, xKey, yKeys);
  const seriesData = (type === "line" || type === "bar") && seriesKeys.length > 0
    ? buildSeriesData(rows, xKey, yKeys, seriesKeys)
    : null;
  const data = seriesData?.rows || rows.slice(0, 100).map((row) => {
    const item: Record<string, unknown> = { ...row, __x: cellValue(row[xKey]) };
    yKeys.forEach((key, index) => {
      item[`__y${index}`] = numericValue(row[key]);
    });
    return item;
  });
  const histogramData = type === "histogram" ? buildHistogram(rows, yKey) : [];

  return (
    <div className="dynamic-chart">
      <div className="dynamic-chart-header">
        <span>{chartSpec?.title || `${yKeys.join(", ")} by ${xKey}`}</span>
      </div>
      <div className="dynamic-chart-canvas">
        <ResponsiveContainer width="100%" height="100%">
          {type === "heatmap" ? (
            <Heatmap rows={rows} xKey={xKey} yKey={yKey} />
          ) : type === "box" ? (
            <BoxPlot rows={rows} yKeys={yKeys} />
          ) : type === "scatter" ? (
            <ScatterChart data={data} margin={{ top: 12, right: 16, bottom: 8, left: 0 }}>
              <CartesianGrid stroke="rgba(71, 85, 105, 0.22)" />
              <XAxis dataKey="__y0" name={yKeys[0]} tick={{ fill: "#94a3b8", fontSize: 11 }} tickLine={false} axisLine={false} />
              <YAxis dataKey={yKeys[1] ? "__y1" : "__y0"} name={yKeys[1] || yKeys[0]} tick={{ fill: "#94a3b8", fontSize: 11 }} tickLine={false} axisLine={false} />
              <Tooltip contentStyle={tooltipStyle} />
              <Scatter name={chartSpec?.title || "Scatter"} data={data} fill={COLORS[0]} />
            </ScatterChart>
          ) : type === "histogram" ? (
            <BarChart data={histogramData} margin={{ top: 12, right: 16, bottom: 8, left: 0 }}>
              <CartesianGrid stroke="rgba(71, 85, 105, 0.22)" vertical={false} />
              <XAxis dataKey="bin" tick={{ fill: "#94a3b8", fontSize: 11 }} tickLine={false} axisLine={false} />
              <YAxis tick={{ fill: "#94a3b8", fontSize: 11 }} tickLine={false} axisLine={false} />
              <Tooltip contentStyle={tooltipStyle} cursor={{ fill: "rgba(99, 102, 241, 0.08)" }} />
              <Bar dataKey="count" name="count" fill={COLORS[2]} radius={[4, 4, 0, 0]} />
            </BarChart>
          ) : type === "bar" ? (
            <BarChart data={data} margin={{ top: 12, right: 16, bottom: 8, left: 0 }}>
              <CartesianGrid stroke="rgba(71, 85, 105, 0.22)" vertical={false} />
              <XAxis dataKey="__x" tick={{ fill: "#94a3b8", fontSize: 11 }} tickLine={false} axisLine={false} />
              <YAxis tick={{ fill: "#94a3b8", fontSize: 11 }} tickLine={false} axisLine={false} />
              <Tooltip contentStyle={tooltipStyle} cursor={{ fill: "rgba(99, 102, 241, 0.08)" }} />
              {(seriesData || yKeys.length > 1) && <Legend wrapperStyle={{ color: "#94a3b8", fontSize: 12 }} />}
              {seriesData
                ? seriesData.traces.map((trace, index) => (
                  <Bar key={trace.key} dataKey={trace.key} name={trace.name} fill={COLORS[index % COLORS.length]} radius={[4, 4, 0, 0]} />
                ))
                : yKeys.map((key, index) => (
                  <Bar key={key} dataKey={`__y${index}`} name={key} fill={COLORS[index % COLORS.length]} radius={[4, 4, 0, 0]} />
                ))}
            </BarChart>
          ) : type === "line" ? (
            <LineChart data={data} margin={{ top: 12, right: 16, bottom: 8, left: 0 }}>
              <CartesianGrid stroke="rgba(71, 85, 105, 0.22)" vertical={false} />
              <XAxis dataKey="__x" tick={{ fill: "#94a3b8", fontSize: 11 }} tickLine={false} axisLine={false} />
              <YAxis tick={{ fill: "#94a3b8", fontSize: 11 }} tickLine={false} axisLine={false} />
              <Tooltip contentStyle={tooltipStyle} />
              <Legend wrapperStyle={{ color: "#94a3b8", fontSize: 12 }} />
              {seriesData
                ? seriesData.traces.map((trace, index) => (
                  <Line
                    key={trace.key}
                    type="monotone"
                    dataKey={trace.key}
                    name={trace.name}
                    stroke={COLORS[(index + 1) % COLORS.length]}
                    strokeWidth={2}
                    dot={false}
                  />
                ))
                : yKeys.map((key, index) => (
                  <Line
                    key={key}
                    type="monotone"
                    dataKey={`__y${index}`}
                    name={key}
                    stroke={COLORS[(index + 1) % COLORS.length]}
                    strokeWidth={2}
                    dot={false}
                  />
                ))}
            </LineChart>
          ) : (
            <PieChart margin={{ top: 6, right: 6, bottom: 6, left: 6 }}>
              <Pie
                data={data}
                dataKey="__y0"
                nameKey="__x"
                cx="50%"
                cy="50%"
                outerRadius="76%"
                innerRadius="42%"
                paddingAngle={1}
              >
                {data.map((_, index) => (
                  <Cell key={index} fill={COLORS[index % COLORS.length]} />
                ))}
              </Pie>
              <Tooltip contentStyle={tooltipStyle} />
              <Legend wrapperStyle={{ color: "#94a3b8", fontSize: 12 }} />
            </PieChart>
          )}
        </ResponsiveContainer>
      </div>
    </div>
  );
}

const tooltipStyle = {
  background: "#111827",
  border: "1px solid rgba(71, 85, 105, 0.55)",
  borderRadius: 6,
  color: "#f1f5f9",
};

function EmptyChart({ title, message }: { title: string; message: string }) {
  return (
    <div className="dynamic-chart dynamic-chart-empty">
      <div className="dynamic-chart-header">
        <span>{title}</span>
      </div>
      <div className="dynamic-chart-empty-message">{message}</div>
    </div>
  );
}

function Heatmap({ rows, xKey, yKey }: { rows: Record<string, unknown>[]; xKey: string; yKey: string }) {
  const values = rows.slice(0, 48).map((row) => numericValue(row[yKey]));
  const max = Math.max(...values, 1);
  return (
    <div className="dynamic-heatmap">
      {rows.slice(0, 48).map((row, index) => {
        const value = numericValue(row[yKey]);
        const opacity = Math.max(0.18, value / max);
        return (
          <div
            key={index}
            className="dynamic-heatmap-cell"
            title={`${cellValue(row[xKey])}: ${value}`}
            style={{ background: `rgba(99, 102, 241, ${opacity})` }}
          >
            <span>{cellValue(row[xKey])}</span>
            <strong>{value}</strong>
          </div>
        );
      })}
    </div>
  );
}

function BoxPlot({ rows, yKeys }: { rows: Record<string, unknown>[]; yKeys: string[] }) {
  const summaries = yKeys.map((key) => ({ key, ...fiveNumber(rows.map((row) => numericValue(row[key]))) }));
  return (
    <div className="dynamic-boxplot">
      {summaries.map((summary, index) => (
        <div key={summary.key} className="dynamic-boxplot-row">
          <span>{summary.key}</span>
          <div className="dynamic-boxplot-track">
            <div
              className="dynamic-boxplot-box"
              style={{
                left: `${summary.q1Percent}%`,
                width: `${Math.max(2, summary.q3Percent - summary.q1Percent)}%`,
                background: COLORS[index % COLORS.length],
              }}
            />
            <div className="dynamic-boxplot-median" style={{ left: `${summary.medianPercent}%` }} />
          </div>
          <small>{summary.min} - {summary.max}</small>
        </div>
      ))}
    </div>
  );
}

interface SeriesData {
  rows: Record<string, unknown>[];
  traces: Array<{ key: string; name: string }>;
}

function seriesColumns(
  value: ChartSpec["series"],
  columns: string[],
  xKey: string,
  yKeys: string[],
): string[] {
  const requested = Array.isArray(value) ? value : value ? [value] : [];
  return requested.filter((key) => columns.includes(key) && key !== xKey && !yKeys.includes(key));
}

function buildSeriesData(
  rows: Record<string, unknown>[],
  xKey: string,
  yKeys: string[],
  seriesKeys: string[],
): SeriesData {
  const byX = new Map<string, Record<string, unknown>>();
  const xOrder: string[] = [];
  const seriesOrder: string[] = [];
  const seriesIndex = new Map<string, number>();

  rows.forEach((row) => {
    const x = cellValue(row[xKey]);
    if (!byX.has(x)) {
      byX.set(x, { __x: x });
      xOrder.push(x);
    }

    const label = seriesLabel(row, seriesKeys);
    if (!seriesIndex.has(label)) {
      seriesIndex.set(label, seriesIndex.size);
      seriesOrder.push(label);
    }

    const record = byX.get(x);
    const currentSeriesIndex = seriesIndex.get(label);
    if (!record || currentSeriesIndex === undefined) return;

    yKeys.forEach((yKey, yIndex) => {
      const traceKey = "__series_" + currentSeriesIndex + "_" + yIndex;
      const current = typeof record[traceKey] === "number" ? (record[traceKey] as number) : 0;
      record[traceKey] = current + numericValue(row[yKey]);
    });
  });

  return {
    rows: xOrder.map((x) => byX.get(x) || { __x: x }),
    traces: seriesOrder.flatMap((label, currentSeriesIndex) => (
      yKeys.map((yKey, yIndex) => ({
        key: "__series_" + currentSeriesIndex + "_" + yIndex,
        name: yKeys.length > 1 ? label + " / " + yKey : label,
      }))
    )),
  };
}

function seriesLabel(row: Record<string, unknown>, seriesKeys: string[]): string {
  return seriesKeys.map((key) => cellValue(row[key]) || "NULL").join(" / ") || "Series";
}

function inferType(columns: string[], numericColumns: string[], requested?: ChartType): ChartType {
  if (requested && requested !== "table") return requested;
  if (!numericColumns.length) return "table";
  if (columns.some((column) => /time|date|day|hour/i.test(column))) return "line";
  return "bar";
}

function buildHistogram(rows: Record<string, unknown>[], key: string) {
  const values = rows.map((row) => numericValue(row[key])).filter((value) => Number.isFinite(value));
  if (!values.length) return [];
  const min = Math.min(...values);
  const max = Math.max(...values);
  const binCount = Math.min(8, Math.max(3, Math.ceil(Math.sqrt(values.length))));
  const width = (max - min || 1) / binCount;
  return Array.from({ length: binCount }, (_, index) => {
    const start = min + index * width;
    const end = index === binCount - 1 ? max : start + width;
    const count = values.filter((value) => value >= start && (index === binCount - 1 ? value <= end : value < end)).length;
    return { bin: `${start.toFixed(1)}-${end.toFixed(1)}`, count };
  });
}

function fiveNumber(values: number[]) {
  const sorted = values.filter((value) => Number.isFinite(value)).sort((a, b) => a - b);
  const min = sorted[0] ?? 0;
  const max = sorted[sorted.length - 1] ?? min;
  const q1 = quantile(sorted, 0.25);
  const median = quantile(sorted, 0.5);
  const q3 = quantile(sorted, 0.75);
  const scale = max - min || 1;
  return {
    min: round(min),
    max: round(max),
    q1Percent: ((q1 - min) / scale) * 100,
    q3Percent: ((q3 - min) / scale) * 100,
    medianPercent: ((median - min) / scale) * 100,
  };
}

function quantile(sorted: number[], q: number): number {
  if (!sorted.length) return 0;
  const position = (sorted.length - 1) * q;
  const base = Math.floor(position);
  const rest = position - base;
  return sorted[base + 1] !== undefined ? sorted[base] + rest * (sorted[base + 1] - sorted[base]) : sorted[base];
}

function round(value: number): number {
  return Math.round(value * 100) / 100;
}

function isNumeric(value: unknown): boolean {
  if (typeof value === "number") return Number.isFinite(value);
  if (typeof value === "string" && value.trim()) return Number.isFinite(Number(value));
  return false;
}

function numericValue(value: unknown): number {
  const number = Number(value);
  return Number.isFinite(number) ? number : 0;
}

function cellValue(value: unknown): string {
  if (value === null || value === undefined) return "NULL";
  if (typeof value === "object") return JSON.stringify(value).slice(0, 120);
  return String(value).slice(0, 120);
}
