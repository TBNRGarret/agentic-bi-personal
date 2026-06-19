import { useEffect, useMemo, useRef } from "react";
import type { ChartSpec, ChartType } from "./DynamicChart";

interface PlotlyChartProps {
  chartSpec?: ChartSpec;
  rows: Record<string, unknown>[];
  typeOverride?: ChartType;
  visualizationDsl: Record<string, unknown>;
}

type PlotlyTrace = Record<string, unknown>;
type PlotlyRuntime = {
  newPlot: (node: HTMLElement, data: unknown[], layout: Record<string, unknown>, config?: Record<string, unknown>) => Promise<unknown>;
  purge: (node: HTMLElement) => void;
  Plots: {
    resize: (node: HTMLElement) => void;
  };
};

const COLORS = ["#2563eb", "#0f766e", "#dc2626", "#9333ea", "#ea580c", "#0891b2", "#65a30d", "#be123c"];

export function PlotlyChart({ chartSpec, rows, typeOverride, visualizationDsl }: PlotlyChartProps) {
  const ref = useRef<HTMLDivElement | null>(null);
  const { data, layout } = useMemo(
    () => buildPlotlyFigure(rows, visualizationDsl, chartSpec, typeOverride),
    [chartSpec, rows, typeOverride, visualizationDsl],
  );

  useEffect(() => {
    if (!ref.current) return undefined;
    const node = ref.current;
    let disposed = false;
    let plotly: PlotlyRuntime | null = null;
    let removeResize: (() => void) | undefined;

    void import("plotly.js-dist-min").then((module) => {
      if (disposed) return;
      plotly = module.default as PlotlyRuntime;
      void plotly.newPlot(node, data, layout, {
        displayModeBar: false,
        responsive: true,
        scrollZoom: false,
      });
      const onResize = () => plotly?.Plots.resize(node);
      window.addEventListener("resize", onResize);
      removeResize = () => window.removeEventListener("resize", onResize);
    });

    return () => {
      disposed = true;
      removeResize?.();
      plotly?.purge(node);
    };
  }, [data, layout]);

  return (
    <div className="dynamic-chart plotly-chart">
      <div className="dynamic-chart-header">
        <span>{String(layout.title || chartSpec?.title || "Chart")}</span>
      </div>
      <div ref={ref} className="plotly-chart-canvas" />
    </div>
  );
}

function buildPlotlyFigure(
  rows: Record<string, unknown>[],
  dsl: Record<string, unknown>,
  chartSpec?: ChartSpec,
  typeOverride?: ChartType,
): { data: PlotlyTrace[]; layout: Record<string, unknown> } {
  const config = objectValue(dsl.config);
  const dslLayout = objectValue(dsl.layout);
  const chartType = typeOverride || stringValue(dsl.chart_type) || chartSpec?.type || "table";
  const title = stringValue(dslLayout.title) || chartSpec?.title || "Chart";
  const baseLayout = {
    ...dslLayout,
    title,
    autosize: true,
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "rgba(0,0,0,0)",
    font: { color: "#cbd5e1", size: 11 },
    margin: { t: 36, r: 14, b: 44, l: 48 },
    legend: { orientation: "h", x: 0, y: -0.18 },
  };

  if (chartType === "empty") {
    return { data: [], layout: baseLayout };
  }

  if (chartType === "table") {
    const columns = stringArrayValue(config.columns) || Object.keys(rows[0] || {});
    return {
      data: [
        {
          type: "table",
          header: { values: columns, fill: { color: "#111827" }, font: { color: "#f8fafc" } },
          cells: {
            values: columns.map((column) => rows.slice(0, 50).map((row) => cellValue(row[column]))),
            fill: { color: "#020617" },
            font: { color: "#cbd5e1" },
          },
        },
      ],
      layout: baseLayout,
    };
  }

  if (chartType === "pie") {
    const labels = stringValue(config.labels) || chartSpec?.x || firstStringColumn(rows);
    const values = stringValue(config.values) || firstYColumn(config, chartSpec, rows);
    return {
      data: [
        {
          type: "pie",
          labels: rows.map((row) => cellValue(row[labels])),
          values: rows.map((row) => numericValue(row[values])),
          hole: 0.42,
          marker: { colors: COLORS },
        },
      ],
      layout: baseLayout,
    };
  }

  if (chartType === "histogram") {
    const x = stringValue(config.x) || firstYColumn(config, chartSpec, rows);
    return {
      data: [{ type: "histogram", x: rows.map((row) => numericValue(row[x])), marker: { color: COLORS[1] }, nbinsx: numberValue(config.nbins) || 20 }],
      layout: { ...baseLayout, xaxis: axis(stringValue(dslLayout.xaxis_title) || x), yaxis: axis("Frequency") },
    };
  }

  if (chartType === "box") {
    const y = stringValue(config.y) || firstYColumn(config, chartSpec, rows);
    const x = stringValue(config.x);
    return {
      data: [
        {
          type: "box",
          y: rows.map((row) => numericValue(row[y])),
          x: x ? rows.map((row) => cellValue(row[x])) : undefined,
          name: y,
          marker: { color: COLORS[3] },
          boxpoints: "outliers",
        },
      ],
      layout: { ...baseLayout, xaxis: axis(x || ""), yaxis: axis(stringValue(dslLayout.yaxis_title) || y) },
    };
  }

  if (chartType === "heatmap") {
    const x = stringValue(config.x) || chartSpec?.x || firstStringColumn(rows);
    const y = stringValue(config.y) || secondStringColumn(rows, x);
    const z = stringValue(config.z) || firstYColumn(config, chartSpec, rows);
    const heatmap = buildHeatmap(rows, x, y, z);
    return {
      data: [{ type: "heatmap", x: heatmap.x, y: heatmap.y, z: heatmap.z, colorscale: "Viridis" }],
      layout: { ...baseLayout, xaxis: axis(stringValue(dslLayout.xaxis_title) || x), yaxis: axis(stringValue(dslLayout.yaxis_title) || y) },
    };
  }

  if (chartType === "scatter") {
    const x = stringValue(config.x) || chartSpec?.x || firstNumericColumn(rows);
    const y = stringValue(config.y) || firstYColumn(config, chartSpec, rows, x);
    return {
      data: [
        {
          type: "scatter",
          mode: stringValue(config.mode) || "markers",
          x: rows.map((row) => numericValue(row[x])),
          y: rows.map((row) => numericValue(row[y])),
          name: y,
          marker: { color: COLORS[0], size: 8 },
        },
      ],
      layout: { ...baseLayout, xaxis: axis(stringValue(dslLayout.xaxis_title) || x), yaxis: axis(stringValue(dslLayout.yaxis_title) || y) },
    };
  }

  const x = stringValue(config.x) || chartSpec?.x || firstStringColumn(rows);
  const yColumns = yColumnsValue(config.y, chartSpec, rows, x);
  const series = seriesColumnsValue(config.series, chartSpec, rows, x, yColumns);
  if (series.length > 0) {
    const yColumn = yColumns[0];
    const seriesValues = unique(rows.map((row) => seriesLabel(row, series)));
    const traces = seriesValues.map((seriesValue, index) => {
      const seriesRows = rows.filter((row) => seriesLabel(row, series) === seriesValue);
      return {
        type: chartType === "line" ? "scatter" : "bar",
        mode: chartType === "line" ? stringValue(config.mode) || "lines+markers" : undefined,
        x: seriesRows.map((row) => cellValue(row[x])),
        y: seriesRows.map((row) => nullableNumber(row[yColumn])),
        name: seriesValue,
        marker: { color: COLORS[index % COLORS.length] },
        line: { color: COLORS[index % COLORS.length], width: 2 },
      };
    });
    return {
      data: traces,
      layout: {
        ...baseLayout,
        barmode: "group",
        xaxis: axis(stringValue(dslLayout.xaxis_title) || x),
        yaxis: axis(stringValue(dslLayout.yaxis_title) || yColumn),
      },
    };
  }
  const mode = chartType === "line" ? stringValue(config.mode) || "lines+markers" : undefined;
  const traces = yColumns.map((column, index) => ({
    type: chartType === "line" ? "scatter" : "bar",
    mode,
    x: rows.map((row) => cellValue(row[x])),
    y: rows.map((row) => nullableNumber(row[column])),
    name: column,
    marker: { color: COLORS[index % COLORS.length] },
    line: { color: COLORS[index % COLORS.length], width: 2 },
  }));
  return {
    data: traces,
    layout: {
      ...baseLayout,
      barmode: "group",
      xaxis: axis(stringValue(dslLayout.xaxis_title) || x),
      yaxis: axis(stringValue(dslLayout.yaxis_title) || "Value"),
    },
  };
}

function buildHeatmap(rows: Record<string, unknown>[], xColumn: string, yColumn: string, zColumn: string) {
  const xValues = unique(rows.map((row) => cellValue(row[xColumn]))).slice(0, 30);
  const yValues = unique(rows.map((row) => cellValue(row[yColumn]))).slice(0, 30);
  const matrix = yValues.map((y) => xValues.map((x) => {
    const matching = rows.filter((row) => cellValue(row[xColumn]) === x && cellValue(row[yColumn]) === y);
    return matching.reduce((total, row) => total + numericValue(row[zColumn]), 0);
  }));
  return { x: xValues, y: yValues, z: matrix };
}

function yColumnsValue(value: unknown, chartSpec: ChartSpec | undefined, rows: Record<string, unknown>[], exclude?: string): string[] {
  const configured = stringArrayValue(value) || (typeof value === "string" ? [value] : undefined);
  const requested = Array.isArray(chartSpec?.y) ? chartSpec.y : chartSpec?.y ? [chartSpec.y] : undefined;
  const candidates = configured || requested || numericColumns(rows).filter((column) => column !== exclude);
  return candidates.length ? candidates.slice(0, 5) : [firstNumericColumn(rows)];
}

function firstYColumn(
  config: Record<string, unknown>,
  chartSpec: ChartSpec | undefined,
  rows: Record<string, unknown>[],
  exclude?: string,
): string {
  return yColumnsValue(config.y, chartSpec, rows, exclude)[0];
}

function firstStringColumn(rows: Record<string, unknown>[]): string {
  const columns = Object.keys(rows[0] || {});
  return columns.find((column) => !numericColumns(rows).includes(column)) || columns[0] || "";
}

function secondStringColumn(rows: Record<string, unknown>[], exclude?: string): string {
  const columns = Object.keys(rows[0] || {});
  return columns.find((column) => column !== exclude && !numericColumns(rows).includes(column)) || columns.find((column) => column !== exclude) || "";
}

function firstNumericColumn(rows: Record<string, unknown>[]): string {
  return numericColumns(rows)[0] || Object.keys(rows[0] || {})[0] || "";
}

function numericColumns(rows: Record<string, unknown>[]): string[] {
  const columns = Object.keys(rows[0] || {});
  return columns.filter((column) => rows.some((row) => Number.isFinite(Number(row[column]))));
}

function axis(title: string) {
  return {
    title,
    gridcolor: "rgba(148, 163, 184, 0.14)",
    zerolinecolor: "rgba(148, 163, 184, 0.18)",
    tickfont: { color: "#94a3b8", size: 10 },
  };
}

function objectValue(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value ? (value as Record<string, unknown>) : {};
}

function stringValue(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value : undefined;
}

function seriesColumnsValue(
  value: unknown,
  chartSpec: ChartSpec | undefined,
  rows: Record<string, unknown>[],
  x: string,
  yColumns: string[],
): string[] {
  const columns = Object.keys(rows[0] || {});
  const configured = stringArrayValue(value) || (typeof value === "string" ? [value] : undefined);
  const requested = Array.isArray(chartSpec?.series) ? chartSpec.series : chartSpec?.series ? [chartSpec.series] : undefined;
  return (configured || requested || []).filter((column) => (
    columns.includes(column) && column !== x && !yColumns.includes(column)
  ));
}

function seriesLabel(row: Record<string, unknown>, series: string[]): string {
  return series.map((column) => cellValue(row[column]) || "NULL").join(" / ") || "Series";
}

function stringArrayValue(value: unknown): string[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const values = value.filter((item): item is string => typeof item === "string" && Boolean(item.trim()));
  return values.length ? values : undefined;
}

function numberValue(value: unknown): number | undefined {
  const number = Number(value);
  return Number.isFinite(number) ? number : undefined;
}

function numericValue(value: unknown): number {
  const number = Number(value);
  return Number.isFinite(number) ? number : 0;
}

function nullableNumber(value: unknown): number | null {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function cellValue(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "object") return JSON.stringify(value).slice(0, 120);
  return String(value).slice(0, 120);
}

function unique(values: string[]): string[] {
  return Array.from(new Set(values));
}
