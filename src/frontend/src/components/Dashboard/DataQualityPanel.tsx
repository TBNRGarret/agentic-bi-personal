import { useEffect, useMemo, useState } from "react";
import { api } from "../../services/api.service";

interface QualitySummary {
  status?: string;
  totalChecks?: number;
  passedCount?: number;
  failedCount?: number;
  warnedCount?: number;
  passRate?: number;
}

interface QualityTable {
  tableName?: string;
  layer?: string;
  status?: string;
  checkCount?: number;
  passedCount?: number;
  failedCount?: number;
  warnedCount?: number;
  failedRows?: number;
  lastCheckedAt?: string;
}

interface QualityCheck {
  runId?: string;
  tableName?: string;
  layer?: string;
  checkName?: string;
  checkType?: string;
  status?: string;
  severity?: string;
  observedValue?: string;
  expectedValue?: string;
  failedRows?: number;
  checkedAt?: string;
}

interface QualityPayload {
  status?: string;
  error?: string;
  latestRun?: { runId?: string; checkedAt?: string } | null;
  summary?: QualitySummary;
  byTable?: QualityTable[];
  recentChecks?: QualityCheck[];
}

export function DataQualityPanel() {
  const [data, setData] = useState<QualityPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");

  const refresh = () => {
    setLoading(true);
    setError("");
    api.dataQuality(200)
      .then((payload) => setData(payload as QualityPayload))
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => { refresh(); }, []);

  const checks = data?.recentChecks || [];
  const visibleChecks = useMemo(() => {
    if (statusFilter === "all") return checks;
    return checks.filter((check) => String(check.status || "") === statusFilter);
  }, [checks, statusFilter]);

  if (loading) {
    return (
      <div className="loading-spinner">
        <div className="spinner" />
        <span>Loading data quality results...</span>
      </div>
    );
  }

  if (error || data?.status === "unavailable") {
    return (
      <div className="empty-state">
        <div className="empty-state-icon">DQ</div>
        <div className="empty-state-text">Failed to load data quality results: {error || data?.error}</div>
        <button className="btn btn-primary" style={{ marginTop: 16 }} onClick={refresh}>Retry</button>
      </div>
    );
  }

  const summary = data?.summary || {};
  const byTable = data?.byTable || [];

  return (
    <div className="fade-in">
      <div className="dashboard-toolbar">
        <div className="alert-summary">
          <strong>{summary.status || "new"}</strong>
          <span>
            Latest server quality run {data?.latestRun?.runId || "not published yet"}
            {data?.latestRun?.checkedAt ? ` at ${formatDate(data.latestRun.checkedAt)}` : ""}
          </span>
        </div>
        <button className="btn btn-secondary" onClick={refresh}>Refresh</button>
      </div>

      <div className="metrics-grid">
        <div className="metric-card">
          <div className="metric-icon emerald">PASS</div>
          <div className="metric-value">{Number(summary.passRate || 0).toFixed(1)}%</div>
          <div className="metric-label">Pass Rate</div>
        </div>
        <div className="metric-card">
          <div className="metric-icon cyan">CHK</div>
          <div className="metric-value">{summary.totalChecks || 0}</div>
          <div className="metric-label">Checks</div>
        </div>
        <div className="metric-card">
          <div className="metric-icon rose">FAIL</div>
          <div className="metric-value">{summary.failedCount || 0}</div>
          <div className="metric-label">Failures</div>
        </div>
        <div className="metric-card">
          <div className="metric-icon amber">WARN</div>
          <div className="metric-value">{summary.warnedCount || 0}</div>
          <div className="metric-label">Warnings</div>
        </div>
      </div>

      <div className="section">
        <div className="section-title">Quality by Table</div>
        <div className="card">
          {byTable.length === 0 ? (
            <div className="empty-state">
              <div className="empty-state-icon">NEW</div>
              <div className="empty-state-text">
                No quality results are available from the current server flow yet.
              </div>
            </div>
          ) : (
            <div style={{ overflowX: "auto" }}>
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Table</th>
                    <th>Layer</th>
                    <th>Status</th>
                    <th>Checks</th>
                    <th>Failed</th>
                    <th>Warned</th>
                    <th>Failed Rows</th>
                    <th>Last Checked</th>
                  </tr>
                </thead>
                <tbody>
                  {byTable.map((table) => (
                    <tr key={`${table.layer}-${table.tableName}`}>
                      <td className="mono">{table.tableName || "unknown"}</td>
                      <td>{table.layer || "-"}</td>
                      <td><StatusBadge status={table.status} /></td>
                      <td>{table.checkCount || 0}</td>
                      <td>{table.failedCount || 0}</td>
                      <td>{table.warnedCount || 0}</td>
                      <td>{table.failedRows || 0}</td>
                      <td>{table.lastCheckedAt ? formatDate(table.lastCheckedAt) : "-"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>

      <div className="section">
        <div className="section-title">Recent Checks</div>
        <div className="dashboard-toolbar compact">
          <select className="filter-select" value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
            <option value="all">All statuses</option>
            <option value="pass">Pass</option>
            <option value="warn">Warn</option>
            <option value="fail">Fail</option>
            <option value="error">Error</option>
            <option value="skipped">Skipped</option>
          </select>
        </div>
        <div className="card">
          <div style={{ overflowX: "auto" }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Status</th>
                  <th>Table</th>
                  <th>Check</th>
                  <th>Type</th>
                  <th>Observed</th>
                  <th>Expected</th>
                  <th>Checked At</th>
                </tr>
              </thead>
              <tbody>
                {visibleChecks.map((check, index) => (
                  <tr key={`${check.runId}-${check.checkName}-${index}`}>
                    <td><StatusBadge status={check.status} /></td>
                    <td className="mono">{check.tableName || "unknown"}</td>
                    <td>{check.checkName || "-"}</td>
                    <td>{check.checkType || "-"}</td>
                    <td>{check.observedValue || String(check.failedRows || 0)}</td>
                    <td>{check.expectedValue || "-"}</td>
                    <td>{check.checkedAt ? formatDate(check.checkedAt) : "-"}</td>
                  </tr>
                ))}
                {visibleChecks.length === 0 && (
                  <tr>
                    <td colSpan={7}>No checks match this filter.</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}

function StatusBadge({ status }: { status?: string }) {
  const normalized = String(status || "unknown").toLowerCase();
  return <span className={`dq-status-badge ${normalized}`}>{normalized}</span>;
}

function formatDate(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}
