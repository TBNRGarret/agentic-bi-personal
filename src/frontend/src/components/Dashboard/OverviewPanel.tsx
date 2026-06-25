import React, { useEffect, useMemo, useState } from "react";
import {
  BarElement,
  CategoryScale,
  Chart as ChartJS,
  Legend,
  LinearScale,
  LineElement,
  PointElement,
  Title,
  Tooltip,
} from "chart.js";
import { Bar, Line } from "react-chartjs-2";
import { api } from "../../services/api.service";

ChartJS.register(
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  BarElement,
  Title,
  Tooltip,
  Legend,
);

type PeriodKey = "7" | "30" | "all";
type ClusterKey = "all" | "llm" | "traces" | "ingestion";

interface OverviewPayload {
  periodDays?: number;
  metrics?: Record<string, number | null>;
  services?: Array<Record<string, unknown>>;
  series?: {
    llmDaily?: Array<Record<string, unknown>>;
    traceDaily?: Array<Record<string, unknown>>;
    ingestionDaily?: Array<Record<string, unknown>>;
    modelCost?: Array<Record<string, unknown>>;
    agentCostDaily?: Array<Record<string, unknown>>;
  };
  agentCost?: {
    todayCostUsd?: number;
    dailyBudgetUsd?: number;
    overBudget?: boolean;
    topQueries?: Array<Record<string, unknown>>;
  };
  alerts?: Array<{ type?: string; severity?: string; message?: string }>;
  metadata?: {
    status?: string;
    tableCount?: number;
    error?: string | null;
  };
}

const CLUSTERS: Record<Exclude<ClusterKey, "all">, { label: string; summary: string }> = {
  llm: {
    label: "LLM Usage",
    summary: "Generations, token volume, model cost and LLM errors",
  },
  traces: {
    label: "Trace Health",
    summary: "Trace throughput, error rate and p95 latency",
  },
  ingestion: {
    label: "Ingestion",
    summary: "Loaded rows, batch success and quarantine pressure",
  },
};

function rows(payload: OverviewPayload | null, key: keyof NonNullable<OverviewPayload["series"]>) {
  const value = payload?.series?.[key];
  return Array.isArray(value) ? value : [];
}

function metric(payload: OverviewPayload | null, key: string): number {
  return numberValue(payload?.metrics?.[key]);
}

function numberValue(input: unknown): number {
  if (typeof input === "number" && Number.isFinite(input)) return input;
  if (typeof input === "string" && input.trim()) {
    const parsed = Number(input.replace(/,/g, ""));
    return Number.isFinite(parsed) ? parsed : 0;
  }
  return 0;
}

function labelValue(input: unknown): string {
  if (input === null || input === undefined || input === "") return "unknown";
  return String(input);
}

function formatNumber(input: number): string {
  return new Intl.NumberFormat().format(Math.round(input));
}

function formatUsd(input: number): string {
  return `$${input.toFixed(input >= 1 ? 2 : 6)}`;
}

function formatPct(input: number): string {
  return `${Math.round(input)}%`;
}

function shortDate(input: unknown): string {
  return labelValue(input).slice(5) || "n/a";
}

function filterByPeriod<T extends Record<string, unknown>>(items: T[], period: PeriodKey): T[] {
  if (period === "all") return items;
  return items.slice(-Number(period));
}

function sumRows(items: Record<string, unknown>[], key: string): number {
  return items.reduce((total, item) => total + numberValue(item[key]), 0);
}

function successRate(success: number, total: number): number {
  return total > 0 ? (success / total) * 100 : 0;
}

function MetricTrend({ label, good = true }: { label: string; good?: boolean }) {
  return (
    <span className="farol-trend-stack">
      <span className={`farol-trend-line ${good ? "good" : "bad"}`}>{label}</span>
      <span className="farol-trend-line neutral">from ClickHouse Gold marts</span>
    </span>
  );
}

export const OverviewPanel: React.FC = () => {
  const [period, setPeriod] = useState<PeriodKey>("30");
  const [cluster, setCluster] = useState<ClusterKey>("all");
  const [data, setData] = useState<OverviewPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const refresh = () => {
    setLoading(true);
    setError("");
    api.overview()
      .then((payload) => setData(payload as OverviewPayload))
      .catch((reason) => setError((reason as Error).message))
      .finally(() => setLoading(false));
  };

  useEffect(refresh, []);

  const llmDaily = useMemo(() => filterByPeriod(rows(data, "llmDaily"), period), [data, period]);
  const traceDaily = useMemo(() => filterByPeriod(rows(data, "traceDaily"), period), [data, period]);
  const ingestionDaily = useMemo(() => filterByPeriod(rows(data, "ingestionDaily"), period), [data, period]);
  const modelCost = rows(data, "modelCost");
  const agentCostDaily = useMemo(
    () => filterByPeriod(rows(data, "agentCostDaily"), period),
    [data, period],
  );
  const agentQueryCount = sumRows(agentCostDaily, "query_count");
  const agentTokenTotal = sumRows(agentCostDaily, "total_tokens");
  const agentCostTotal = sumRows(agentCostDaily, "estimated_cost_usd");
  const averageAgentCost = agentQueryCount > 0 ? agentCostTotal / agentQueryCount : 0;
  const topAgentQueries = data?.agentCost?.topQueries || [];
  const services = data?.services || [];

  const llmTotal = sumRows(llmDaily, "generation_count");
  const tokenTotal = sumRows(llmDaily, "total_tokens");
  const costTotal = sumRows(llmDaily, "total_cost_usd");
  const traceTotal = sumRows(traceDaily, "trace_count");
  const traceErrors = sumRows(traceDaily, "error_count");
  const loadedRows = sumRows(ingestionDaily, "loaded_row_count");
  const loadedBatches = sumRows(ingestionDaily, "loaded_batch_count");
  const totalBatches = sumRows(ingestionDaily, "batch_count");

  const clusterStats = {
    llm: {
      total: llmTotal,
      success: successRate(llmTotal - sumRows(llmDaily, "error_count"), llmTotal),
      cost: costTotal,
    },
    traces: {
      total: traceTotal,
      success: successRate(traceTotal - traceErrors, traceTotal),
      cost: metric(data, "p95_latency_ms"),
    },
    ingestion: {
      total: totalBatches,
      success: successRate(loadedBatches, totalBatches),
      cost: loadedRows,
    },
  };

  const chartOptions = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: { labels: { color: "#a6adbb" } },
      title: { display: false },
    },
    scales: {
      x: { ticks: { color: "#9aa3b2" }, grid: { color: "rgba(255,255,255,0.06)" } },
      y: { ticks: { color: "#9aa3b2" }, grid: { color: "rgba(255,255,255,0.06)" } },
    },
  };

  const llmCostData = {
    labels: agentCostDaily.map((row) => shortDate(row.metric_date)),
    datasets: [
      {
        label: "Agent cost",
        data: agentCostDaily.map((row) => numberValue(row.estimated_cost_usd)),
        borderColor: "#f59e0b",
        backgroundColor: "rgba(245, 158, 11, 0.18)",
        tension: 0.35,
      },
      {
        label: "Agent queries",
        data: agentCostDaily.map((row) => numberValue(row.query_count)),
        borderColor: "#38bdf8",
        backgroundColor: "rgba(56, 189, 248, 0.16)",
        tension: 0.35,
      },
    ],
  };

  const tokensData = {
    labels: llmDaily.map((row) => shortDate(row.metric_date)),
    datasets: [
      {
        label: "Tokens",
        data: llmDaily.map((row) => numberValue(row.total_tokens)),
        borderColor: "#38bdf8",
        backgroundColor: "rgba(56, 189, 248, 0.18)",
        tension: 0.35,
      },
      {
        label: "Generations",
        data: llmDaily.map((row) => numberValue(row.generation_count)),
        borderColor: "#4ade80",
        backgroundColor: "rgba(74, 222, 128, 0.16)",
        tension: 0.35,
      },
    ],
  };

  const traceHealthData = {
    labels: traceDaily.map((row) => shortDate(row.metric_date)),
    datasets: [
      {
        label: "Traces",
        data: traceDaily.map((row) => numberValue(row.trace_count)),
        backgroundColor: "rgba(99, 102, 241, 0.72)",
        borderRadius: 6,
      },
      {
        label: "Errors",
        data: traceDaily.map((row) => numberValue(row.error_count)),
        backgroundColor: "rgba(244, 63, 94, 0.72)",
        borderRadius: 6,
      },
    ],
  };

  const ingestionData = {
    labels: ingestionDaily.map((row) => shortDate(row.metric_date)),
    datasets: [
      {
        label: "Loaded rows",
        data: ingestionDaily.map((row) => numberValue(row.loaded_row_count)),
        borderColor: "#10b981",
        backgroundColor: "rgba(16, 185, 129, 0.18)",
        tension: 0.35,
      },
      {
        label: "Failed batches",
        data: ingestionDaily.map((row) => numberValue(row.failed_batch_count)),
        borderColor: "#f43f5e",
        backgroundColor: "rgba(244, 63, 94, 0.16)",
        tension: 0.35,
      },
    ],
  };

  const modelCostData = {
    labels: modelCost.slice(0, 8).map((row) => labelValue(row.model)),
    datasets: [
      {
        label: "Cost by model",
        data: modelCost.slice(0, 8).map((row) => numberValue(row.total_cost_usd)),
        backgroundColor: ["#ff6b1a", "#4ade80", "#38bdf8", "#f59e0b", "#a855f7", "#14b8a6", "#eab308", "#f43f5e"],
        borderRadius: 6,
      },
    ],
  };

  if (loading) {
    return (
      <div className="farol-overview">
        <div className="loading">Loading overview...</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="empty-state">
        <div className="empty-state-icon">!</div>
        <div className="empty-state-text">{error}</div>
        <button className="btn btn-primary" type="button" onClick={refresh}>Retry</button>
      </div>
    );
  }

  return (
    <div className="farol-overview">
      <div className="farol-section-header">
        <div>
          <h1>Overview</h1>
          <p>Agentic BI Gold metrics, LLM cost, trace health and ingestion status.</p>
        </div>
        <button className="btn btn-secondary" type="button" onClick={refresh}>Refresh</button>
      </div>

      {(data?.alerts || []).map((alert) => (
        <div key={`${alert.type}-${alert.message}`} className="alert-banner farol-inline-alert">
          <div>
            <div className="alert-banner-title">Agent budget alert</div>
            <div className="alert-banner-text">{alert.message}</div>
          </div>
        </div>
      ))}

      <div className="farol-overview-toolbar">
        <label className="farol-filter-group">
          <span className="farol-filter-label">Period</span>
          <select className="farol-filter-select" value={period} onChange={(event) => setPeriod(event.target.value as PeriodKey)}>
            <option value="7">Last 7 days</option>
            <option value="30">Last 30 days</option>
            <option value="all">All time</option>
          </select>
        </label>
        <label className="farol-filter-group">
          <span className="farol-filter-label">Domain</span>
          <select className="farol-filter-select" value={cluster} onChange={(event) => setCluster(event.target.value as ClusterKey)}>
            <option value="all">All domains</option>
            <option value="llm">LLM Usage</option>
            <option value="traces">Trace Health</option>
            <option value="ingestion">Ingestion</option>
          </select>
        </label>
        <div className="farol-filter-group">
          <span className="farol-filter-label">Catalog</span>
          <span className="farol-filter-select">
            DataHub {data?.metadata?.status || "unknown"} / {data?.metadata?.tableCount || 0} tables
          </span>
        </div>
      </div>

      <div className="farol-cluster-grid">
        {(Object.keys(CLUSTERS) as Array<Exclude<ClusterKey, "all">>).map((key) => {
          const stat = clusterStats[key];
          const info = CLUSTERS[key];
          const third = key === "llm" ? formatUsd(stat.cost) : key === "traces" ? `${stat.cost.toFixed(1)} ms p95` : `${formatNumber(stat.cost)} rows`;
          return (
            <button
              key={key}
              type="button"
              className={`farol-cluster-card ${cluster === key ? "active" : ""}`}
              onClick={() => setCluster(key)}
            >
              <span className="farol-cluster-name">{info.label}</span>
              <span className="farol-cluster-meta">
                {formatNumber(stat.total)} events / {formatPct(stat.success)} healthy / {third}
              </span>
              <span className="farol-cluster-agents">{info.summary}</span>
            </button>
          );
        })}
      </div>

      <div className="farol-metrics-grid">
        <div className="farol-metric-card">
          <span className="farol-metric-label">LLM Generations</span>
          <span className="farol-metric-value">{formatNumber(llmTotal)}</span>
          <MetricTrend label={`${formatNumber(tokenTotal)} tokens`} />
        </div>
        <div className="farol-metric-card">
          <span className="farol-metric-label">Estimated Cost</span>
          <span className="farol-metric-value">{formatUsd(costTotal)}</span>
          <MetricTrend label={`${formatNumber(sumRows(llmDaily, "error_count"))} LLM errors`} good={sumRows(llmDaily, "error_count") === 0} />
        </div>
        <div className="farol-metric-card">
          <span className="farol-metric-label">Trace Success</span>
          <span className="farol-metric-value">{formatPct(successRate(traceTotal - traceErrors, traceTotal))}</span>
          <MetricTrend label={`${formatNumber(traceTotal)} traces`} good={traceErrors === 0} />
        </div>
        <div className="farol-metric-card">
          <span className="farol-metric-label">Loaded Rows</span>
          <span className="farol-metric-value">{formatNumber(loadedRows)}</span>
          <MetricTrend label={`${formatNumber(loadedBatches)} loaded batches`} good={totalBatches === loadedBatches} />
        </div>
      </div>

      <div className="farol-charts-grid">
        {(cluster === "all" || cluster === "llm") && (
          <div className="farol-chart-card wide">
            <div className="farol-card-header">
              <h3>Agent Cost</h3>
              <span className={`farol-status-pill ${data?.agentCost?.overBudget ? "bad" : "good"}`}>
                {data?.agentCost?.overBudget ? "Over budget" : "On budget"}
              </span>
            </div>
            <div className="farol-cost-kpi-grid">
              <div className="farol-cost-kpi">
                <span className="farol-cost-kpi-label">Period Cost</span>
                <span className="farol-cost-kpi-value">{formatUsd(agentCostTotal)}</span>
              </div>
              <div className="farol-cost-kpi">
                <span className="farol-cost-kpi-label">Tokens</span>
                <span className="farol-cost-kpi-value">{formatNumber(agentTokenTotal)}</span>
              </div>
              <div className="farol-cost-kpi">
                <span className="farol-cost-kpi-label">Queries</span>
                <span className="farol-cost-kpi-value">{formatNumber(agentQueryCount)}</span>
              </div>
              <div className="farol-cost-kpi">
                <span className="farol-cost-kpi-label">Avg / query</span>
                <span className="farol-cost-kpi-value">{formatUsd(averageAgentCost)}</span>
              </div>
            </div>
            <div className="farol-chart-container">
              <Line data={llmCostData} options={chartOptions} />
            </div>
          </div>
        )}

        {(cluster === "all" || cluster === "llm") && (
          <div className="farol-chart-card wide">
            <div className="farol-card-header"><h3>Top Expensive Agent Queries</h3></div>
            <div className="farol-table-wrap">
              <table className="farol-data-table">
                <thead><tr><th>Query</th><th>Model</th><th>Tokens</th><th>Cost</th></tr></thead>
                <tbody>
                  {topAgentQueries.slice(0, 8).map((query, index) => (
                    <tr key={`${labelValue(query.query_id)}-${index}`}>
                      <td>{labelValue(query.question).slice(0, 90)}</td>
                      <td>{labelValue(query.model)}</td>
                      <td>{formatNumber(numberValue(query.total_tokens))}</td>
                      <td>{formatUsd(numberValue(query.estimated_cost_usd))}</td>
                    </tr>
                  ))}
                  {!topAgentQueries.length && <tr><td colSpan={4}>No agent cost records yet.</td></tr>}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {(cluster === "all" || cluster === "llm") && (
          <div className="farol-chart-card wide">
            <div className="farol-card-header">
              <h3>Cost by Model</h3>
            </div>
            <div className="farol-chart-container">
              <Bar data={modelCostData} options={chartOptions} />
            </div>
          </div>
        )}

        {(cluster === "all" || cluster === "traces") && (
          <div className="farol-chart-card">
            <div className="farol-card-header">
              <h3>Trace Health</h3>
            </div>
            <div className="farol-chart-container">
              <Bar data={traceHealthData} options={chartOptions} />
            </div>
          </div>
        )}

        {(cluster === "all" || cluster === "llm") && (
          <div className="farol-chart-card">
            <div className="farol-card-header">
              <h3>Tokens and Generations</h3>
            </div>
            <div className="farol-chart-container">
              <Line data={tokensData} options={chartOptions} />
            </div>
          </div>
        )}

        {(cluster === "all" || cluster === "ingestion") && (
          <div className="farol-chart-card">
            <div className="farol-card-header">
              <h3>Ingestion Throughput</h3>
            </div>
            <div className="farol-chart-container">
              <Line data={ingestionData} options={chartOptions} />
            </div>
          </div>
        )}

        {(cluster === "all" || cluster === "traces") && (
          <div className="farol-chart-card">
            <div className="farol-card-header">
              <h3>Service Error Pressure</h3>
            </div>
            <div className="farol-table-wrap">
              <table className="farol-data-table">
                <thead>
                  <tr>
                    <th>Service</th>
                    <th>Error Logs</th>
                    <th>Error Spans</th>
                    <th>P95 Span</th>
                  </tr>
                </thead>
                <tbody>
                  {services.map((service, index) => (
                    <tr key={`${labelValue(service.service_name)}-${index}`}>
                      <td>{labelValue(service.service_name)}</td>
                      <td>{formatNumber(numberValue(service.error_log_count))}</td>
                      <td>{formatNumber(numberValue(service.error_span_count))}</td>
                      <td>{numberValue(service.p95_span_duration_ms).toFixed(1)} ms</td>
                    </tr>
                  ))}
                  {!services.length && (
                    <tr>
                      <td colSpan={4}>No service metrics are available.</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
