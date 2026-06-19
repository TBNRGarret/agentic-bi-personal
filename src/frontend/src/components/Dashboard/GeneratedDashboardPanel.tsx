import { Database, LayoutDashboard, Play, SlidersHorizontal } from "lucide-react";
import { useState } from "react";
import { DynamicChart, type ChartSpec } from "../Charts/DynamicChart";
import { api } from "../../services/api.service";

type Target = "auto" | "dwh" | "clickhouse";

interface GeneratedDashboard {
  status?: string;
  title?: string;
  queryId?: string;
  panelCount?: number;
  successfulPanelCount?: number;
  panels?: GeneratedPanel[];
  generatedAt?: string;
}

interface GeneratedPanel {
  id?: string;
  title?: string;
  question?: string;
  status?: string;
  answer?: string;
  target?: string;
  sql?: string;
  rowCount?: number;
  rows?: Record<string, unknown>[];
  chartSpec?: ChartSpec;
  visualizationDsl?: Record<string, unknown>;
}

function cellValue(value: unknown): string {
  if (value === null || value === undefined) return "NULL";
  if (typeof value === "object") return JSON.stringify(value).slice(0, 140);
  return String(value).slice(0, 180);
}

function rows(panel: GeneratedPanel): Record<string, unknown>[] {
  return Array.isArray(panel.rows) ? panel.rows : [];
}

function statusClass(status?: string): string {
  if (status === "success") return "success";
  if (status === "needs_clarification") return "warning";
  return "error";
}

function ResultPreview({ rows }: { rows: Record<string, unknown>[] }) {
  if (!rows.length) return null;
  const columns = Object.keys(rows[0]).slice(0, 8);
  return (
    <div className="generated-dashboard-table">
      <table className="data-table">
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column}>{column}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.slice(0, 6).map((row, index) => (
            <tr key={index}>
              {columns.map((column) => (
                <td key={column}>{cellValue(row[column])}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function DashboardPanel({ panel }: { panel: GeneratedPanel }) {
  const panelRows = rows(panel);
  const chartSpec = panel.chartSpec || { type: "table" };
  const chartType = chartSpec.type || "table";
  return (
    <section className="generated-dashboard-panel">
      <div className="generated-dashboard-panel-header">
        <div>
          <h3>{panel.title || "Panel"}</h3>
          <span>{panel.target || "auto"} - {panel.rowCount ?? panelRows.length} rows</span>
        </div>
        <span className={`generated-dashboard-status ${statusClass(panel.status)}`}>
          {panel.status || "unknown"}
        </span>
      </div>

      {panel.answer && <p className="generated-dashboard-answer">{panel.answer}</p>}

      {panelRows.length > 0 && chartType !== "table" && (
        <DynamicChart
          chartSpec={chartSpec}
          rows={panelRows}
          visualizationDsl={panel.visualizationDsl}
        />
      )}
      <ResultPreview rows={panelRows} />

      {panel.sql && (
        <details className="generated-dashboard-sql">
          <summary>SQL</summary>
          <pre>{panel.sql}</pre>
        </details>
      )}
    </section>
  );
}

export function GeneratedDashboardPanel() {
  const [question, setQuestion] = useState("Show me a dashboard of service health");
  const [target, setTarget] = useState<Target>("auto");
  const [maxPanels, setMaxPanels] = useState(3);
  const [dashboard, setDashboard] = useState<GeneratedDashboard | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const generate = async () => {
    const prompt = question.trim();
    if (!prompt || loading) return;
    setLoading(true);
    setError("");
    setDashboard(null);
    try {
      const data = await api.generateDashboard(prompt, target, 100, maxPanels);
      setDashboard(data as GeneratedDashboard);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fade-in generated-dashboard-page">
      <div className="generated-dashboard-toolbar">
        <div className="generated-dashboard-input">
          <LayoutDashboard size={17} />
          <input
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                generate();
              }
            }}
            disabled={loading}
          />
        </div>
        <label className="generated-dashboard-control">
          <Database size={14} />
          <select value={target} onChange={(event) => setTarget(event.target.value as Target)}>
            <option value="auto">auto (ClickHouse gold)</option>
            <option value="clickhouse">clickhouse</option>
            <option value="dwh">dwh</option>
          </select>
        </label>
        <label className="generated-dashboard-control">
          <SlidersHorizontal size={14} />
          <select value={maxPanels} onChange={(event) => setMaxPanels(Number(event.target.value))}>
            <option value={2}>2 panels</option>
            <option value={3}>3 panels</option>
            <option value={4}>4 panels</option>
          </select>
        </label>
        <button className="btn btn-primary" type="button" onClick={generate} disabled={loading || !question.trim()}>
          <Play size={15} />
          {loading ? "Generating" : "Generate"}
        </button>
      </div>

      {error && <div className="query-error">{error}</div>}

      {loading && (
        <div className="loading-spinner">
          <div className="spinner" />
          <span>Generating dashboard...</span>
        </div>
      )}

      {dashboard && (
        <>
          <div className="generated-dashboard-summary">
            <div>
              <h2>{dashboard.title || "Generated BI dashboard"}</h2>
              <span>
                {dashboard.successfulPanelCount ?? 0}/{dashboard.panelCount ?? 0} panels - {dashboard.queryId || ""}
              </span>
            </div>
            <span className={`generated-dashboard-status ${statusClass(dashboard.status)}`}>
              {dashboard.status || "unknown"}
            </span>
          </div>

          <div className="generated-dashboard-grid">
            {(dashboard.panels || []).map((panel, index) => (
              <DashboardPanel key={panel.id || index} panel={panel} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}
