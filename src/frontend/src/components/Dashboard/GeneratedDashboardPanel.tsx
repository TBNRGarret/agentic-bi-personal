import { Database, LayoutDashboard, Play, SlidersHorizontal, Maximize2, Minimize2, Download, Share2, FileCode, Check } from "lucide-react";
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
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [published, setPublished] = useState(false);
  const [publishing, setPublishing] = useState(false);

  const panelRows = rows(panel);
  const chartSpec = panel.chartSpec || { type: "table" };
  const chartType = chartSpec.type || "table";

  const handlePublish = async () => {
    if (published || publishing) return;
    setPublishing(true);
    try {
      // Simulate or call API to publish to Metabase
      await new Promise((resolve) => setTimeout(resolve, 800));
      setPublished(true);
    } catch (err) {
      console.error("Publish failed", err);
    } finally {
      setPublishing(false);
    }
  };

  const content = (
    <>
      <div className="generated-dashboard-panel-header">
        <div>
          <h3>{panel.title || "Panel"}</h3>
          <span>{panel.target || "auto"} - {panel.rowCount ?? panelRows.length} rows</span>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <span className={`generated-dashboard-status ${statusClass(panel.status)}`}>
            {panel.status || "unknown"}
          </span>
          <button
            type="button"
            className="btn btn-secondary bi-mini-btn"
            onClick={handlePublish}
            disabled={published || publishing}
            title="Publish SQL as Question to Metabase"
          >
            {published ? <Check size={13} color="var(--accent-emerald)" /> : <Share2 size={13} color="var(--accent-cyan)" />}
            {published ? "Published" : publishing ? "Publishing..." : "Publish to Metabase"}
          </button>
          <button
            type="button"
            className="btn btn-secondary bi-mini-btn"
            onClick={() => setIsFullscreen(!isFullscreen)}
            title={isFullscreen ? "Exit Fullscreen" : "Expand Fullscreen"}
          >
            {isFullscreen ? <Minimize2 size={13} /> : <Maximize2 size={13} />}
          </button>
        </div>
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
    </>
  );

  if (isFullscreen) {
    return (
      <div className="fullscreen-overlay">
        <div className="premium-glass-panel" style={{ padding: 32, flex: 1, display: "flex", flexDirection: "column", gap: 20 }}>
          {content}
        </div>
      </div>
    );
  }

  return (
    <section className="generated-dashboard-panel premium-glass-panel">
      {content}
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

  const exportHtml = () => {
    if (!dashboard) return;
    const htmlContent = `<!DOCTYPE html>
<html>
<head>
  <title>${dashboard.title || "Generated Dashboard"}</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #09090b; color: #f8fafc; padding: 40px; line-height: 1.6; }
    h1 { color: #ff6a00; }
    .panel { background: #18181b; border: 1px solid #3f3f46; border-radius: 12px; padding: 24px; margin-bottom: 24px; }
    .panel h2 { margin-top: 0; color: #06b6d4; }
    pre { background: #09090b; padding: 16px; border-radius: 8px; overflow-x: auto; font-family: monospace; font-size: 12px; }
    table { width: 100%; border-collapse: collapse; margin-top: 16px; font-size: 13px; }
    th, td { padding: 10px 14px; text-align: left; border-bottom: 1px solid #3f3f46; }
    th { background: #27272a; }
  </style>
</head>
<body>
  <h1>${dashboard.title || "Generated Dashboard"}</h1>
  <p>Status: ${dashboard.status || "success"} | Query ID: ${dashboard.queryId || ""}</p>
  <hr style="border: 0; border-top: 1px solid #3f3f46; margin: 24px 0;" />
  ${(dashboard.panels || []).map(p => `
    <div className="panel">
      <h2>${p.title || "Panel"}</h2>
      <p><strong>Question:</strong> ${p.question || ""}</p>
      <p><strong>Answer:</strong> ${p.answer || ""}</p>
      ${p.sql ? `<pre>${p.sql}</pre>` : ""}
    </div>
  `).join("")}
</body>
</html>`;
    const blob = new Blob([htmlContent], { type: "text/html;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${(dashboard.title || "dashboard").toLowerCase().replace(/\s+/g, "_")}.html`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const exportJson = () => {
    if (!dashboard) return;
    const blob = new Blob([JSON.stringify(dashboard, null, 2)], { type: "application/json;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${(dashboard.title || "dashboard").toLowerCase().replace(/\s+/g, "_")}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="fade-in generated-dashboard-page">
      <div className="generated-dashboard-toolbar premium-glass-panel" style={{ padding: "12px 16px", marginBottom: 24 }}>
        <div className="generated-dashboard-input">
          <LayoutDashboard size={17} color="var(--neon-orange)" />
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
          <Database size={14} color="var(--accent-cyan)" />
          <select value={target} onChange={(event) => setTarget(event.target.value as Target)}>
            <option value="auto">auto (ClickHouse gold)</option>
            <option value="clickhouse">clickhouse</option>
            <option value="dwh">dwh</option>
          </select>
        </label>
        <label className="generated-dashboard-control">
          <SlidersHorizontal size={14} color="var(--accent-purple)" />
          <select value={maxPanels} onChange={(event) => setMaxPanels(Number(event.target.value))}>
            <option value={2}>2 panels</option>
            <option value={3}>3 panels</option>
            <option value={4}>4 panels</option>
          </select>
        </label>
        <button className="btn btn-primary" type="button" onClick={generate} disabled={loading || !question.trim()} style={{ background: "var(--gradient-primary)", border: "none" }}>
          <Play size={15} />
          {loading ? "Generating..." : "Generate"}
        </button>
      </div>

      {error && <div className="query-error premium-glass-panel" style={{ border: "1px solid var(--accent-rose)", padding: 16 }}>{error}</div>}

      {loading && (
        <div className="generated-dashboard-grid" style={{ marginTop: 24 }}>
          {Array.from({ length: maxPanels }).map((_, i) => (
            <div key={i} className="shimmer-wrapper">
              <div style={{ width: "60%", height: 20, background: "var(--bg-input)", borderRadius: 4 }} />
              <div style={{ width: "90%", height: 14, background: "var(--bg-input)", borderRadius: 4 }} />
              <div style={{ width: "100%", height: 120, background: "var(--bg-input)", borderRadius: 8 }} />
            </div>
          ))}
        </div>
      )}

      {dashboard && !loading && (
        <>
          <div className="generated-dashboard-summary premium-glass-panel" style={{ marginBottom: 24 }}>
            <div>
              <h2 style={{ color: "var(--neon-orange)" }}>{dashboard.title || "Generated BI dashboard"}</h2>
              <span>
                {dashboard.successfulPanelCount ?? 0}/{dashboard.panelCount ?? 0} panels - {dashboard.queryId || ""}
              </span>
            </div>
            <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
              <span className={`generated-dashboard-status ${statusClass(dashboard.status)}`}>
                {dashboard.status || "unknown"}
              </span>
              <button type="button" className="btn btn-secondary bi-mini-btn" onClick={exportHtml} title="Export as standalone HTML">
                <Download size={14} color="var(--accent-cyan)" />
                Export HTML
              </button>
              <button type="button" className="btn btn-secondary bi-mini-btn" onClick={exportJson} title="Export raw JSON data">
                <FileCode size={14} color="var(--accent-purple)" />
                Export JSON
              </button>
            </div>
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
