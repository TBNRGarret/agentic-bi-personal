import { useEffect, useState } from "react";
import { api } from "../../services/api.service";
import { RefreshCw, ExternalLink, Copy, Check, ShieldCheck, Activity, Timer } from "lucide-react";

interface Dashboard {
  id?: number;
  name?: string;
  description?: string;
  public_uuid?: string;
  embed_url?: string;
}

interface DashboardsResponse {
  dashboards?: Dashboard[];
  metabaseUrl?: string;
}

const METABASE_URL = import.meta.env.VITE_METABASE_URL || "http://localhost:3000";

export function MetabasePanel() {
  const [dashboards, setDashboards] = useState<Dashboard[]>([]);
  const [metabaseUrl, setMetabaseUrl] = useState(METABASE_URL);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [activeEmbed, setActiveEmbed] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [refreshInterval, setRefreshInterval] = useState<number>(0);
  const [refreshKey, setRefreshKey] = useState(0);
  const [healthStatus, setHealthStatus] = useState({ metabase: "checking", clickhouse: "checking" });

  useEffect(() => {
    api.metabaseDashboards()
      .then((payload) => {
        const response = payload as DashboardsResponse;
        const available = response.dashboards || [];
        setDashboards(available);
        const firstEmbed = available.find((dashboard) => dashboard.embed_url)?.embed_url;
        if (firstEmbed) setActiveEmbed(firstEmbed);
        if (response.metabaseUrl) setMetabaseUrl(response.metabaseUrl);
        setHealthStatus({ metabase: "online", clickhouse: "online" });
      })
      .catch((reason) => {
        setError((reason as Error).message);
        setHealthStatus({ metabase: "error", clickhouse: "online" });
      })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!refreshInterval || !activeEmbed) return;
    const timer = setInterval(() => {
      setRefreshKey((prev) => prev + 1);
    }, refreshInterval * 1000);
    return () => clearInterval(timer);
  }, [refreshInterval, activeEmbed]);

  const handleCopy = () => {
    if (!activeEmbed) return;
    navigator.clipboard?.writeText(activeEmbed);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  if (loading) {
    return (
      <div className="loading-spinner">
        <div className="spinner" />
        <span>Loading Metabase dashboards...</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="empty-state premium-glass-panel" style={{ border: "1px solid var(--accent-rose)", padding: 32 }}>
        <div className="empty-state-icon" style={{ color: "var(--accent-rose)" }}>!</div>
        <div className="empty-state-text">Failed to load dashboards: {error}</div>
      </div>
    );
  }

  if (activeEmbed) {
    const activeDash = dashboards.find((d) => d.embed_url === activeEmbed);
    return (
      <div className="fade-in">
        <div className="top-action-bar premium-glass-panel">
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <button className="btn btn-secondary" type="button" onClick={() => setActiveEmbed(null)}>
              ← Back to Dashboards
            </button>
            <span style={{ fontWeight: 600, color: "var(--text-primary)" }}>
              {activeDash?.name || "Embedded Dashboard"}
            </span>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--text-secondary)" }}>
              <Timer size={14} color="var(--neon-orange)" />
              Auto-refresh:
              <select
                value={refreshInterval}
                onChange={(e) => setRefreshInterval(Number(e.target.value))}
                style={{ background: "var(--bg-input)", color: "var(--text-primary)", border: "1px solid var(--border-color)", borderRadius: 4, padding: "2px 6px" }}
              >
                <option value={0}>Off</option>
                <option value={30}>30s</option>
                <option value={60}>1m</option>
                <option value={300}>5m</option>
              </select>
            </label>
            <button
              type="button"
              className="btn btn-secondary bi-mini-btn"
              onClick={() => setRefreshKey((prev) => prev + 1)}
              title="Force reload dashboard"
            >
              <RefreshCw size={13} />
              Refresh
            </button>
            <button
              type="button"
              className="btn btn-secondary bi-mini-btn"
              onClick={handleCopy}
              title="Copy embed URL"
            >
              {copied ? <Check size={13} color="var(--accent-emerald)" /> : <Copy size={13} />}
              {copied ? "Copied!" : "Copy Link"}
            </button>
            <a
              href={activeEmbed}
              target="_blank"
              rel="noreferrer"
              className="btn btn-secondary bi-mini-btn"
              style={{ textDecoration: "none" }}
              title="Open direct embed URL in new tab"
            >
              <ExternalLink size={13} color="var(--accent-cyan)" />
              Open Direct
            </a>
          </div>
        </div>
        <iframe
          key={refreshKey}
          className="embed-frame premium-glass-panel"
          src={activeEmbed}
          title="Metabase Dashboard"
          style={{ border: "none", width: "100%", height: "calc(100vh - 180px)", borderRadius: 12 }}
        />
      </div>
    );
  }

  const embeddable = dashboards.filter((dashboard) => dashboard.embed_url);

  return (
    <div className="fade-in">
      <div className="premium-glass-panel" style={{ padding: "16px 24px", marginBottom: 24, display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 12 }}>
        <div>
          <h2 style={{ color: "var(--text-primary)", fontSize: 18, margin: 0 }}>Metabase BI Hub</h2>
          <span style={{ color: "var(--text-muted)", fontSize: 12 }}>Professional reporting powered by ClickHouse Gold analytics</span>
        </div>
        <div style={{ display: "flex", gap: 16, alignItems: "center" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12 }}>
            <Activity size={15} color={healthStatus.metabase === "online" ? "var(--accent-emerald)" : "var(--accent-rose)"} />
            <span style={{ color: "var(--text-secondary)" }}>Metabase:</span>
            <strong style={{ color: healthStatus.metabase === "online" ? "var(--accent-emerald)" : "var(--accent-rose)", textTransform: "uppercase" }}>{healthStatus.metabase}</strong>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12 }}>
            <ShieldCheck size={15} color={healthStatus.clickhouse === "online" ? "var(--accent-cyan)" : "var(--accent-rose)"} />
            <span style={{ color: "var(--text-secondary)" }}>ClickHouse Gold:</span>
            <strong style={{ color: healthStatus.clickhouse === "online" ? "var(--accent-cyan)" : "var(--accent-rose)", textTransform: "uppercase" }}>{healthStatus.clickhouse}</strong>
          </div>
        </div>
      </div>

      <div className="section">
        <div className="section-title" style={{ color: "var(--neon-orange)", marginBottom: 16 }}>Quick Access</div>
        <div className="metrics-grid">
          <a href={metabaseUrl} target="_blank" rel="noreferrer" className="kpi-card" style={{ textDecoration: "none" }}>
            <div className="kpi-header">
              <span className="kpi-title">Full BI Platform</span>
              <div className="kpi-icon orange">BI</div>
            </div>
            <div className="kpi-value" style={{ fontSize: 20 }}>Open Metabase</div>
            <div className="trend-indicator good">Direct root access</div>
          </a>
          <a href={`${metabaseUrl}/collection/root`} target="_blank" rel="noreferrer" className="kpi-card" style={{ textDecoration: "none" }}>
            <div className="kpi-header">
              <span className="kpi-title">Browse Dashboards</span>
              <div className="kpi-icon cyan">ALL</div>
            </div>
            <div className="kpi-value" style={{ fontSize: 20 }}>Collections</div>
            <div className="trend-indicator good">Organized reports</div>
          </a>
          <a href={`${metabaseUrl}/question#new`} target="_blank" rel="noreferrer" className="kpi-card" style={{ textDecoration: "none" }}>
            <div className="kpi-header">
              <span className="kpi-title">Create Query</span>
              <div className="kpi-icon emerald">NEW</div>
            </div>
            <div className="kpi-value" style={{ fontSize: 20 }}>New Question</div>
            <div className="trend-indicator good">Visual query builder</div>
          </a>
        </div>
      </div>

      {embeddable.length > 0 ? (
        <div className="section" style={{ marginTop: 32 }}>
          <div className="section-title" style={{ color: "var(--accent-cyan)", marginBottom: 16 }}>Public Dashboards</div>
          <div className="premium-card-grid">
            {embeddable.map((dashboard, index) => (
              <button
                key={dashboard.id || index}
                type="button"
                className="interactive-card"
                onClick={() => dashboard.embed_url && setActiveEmbed(dashboard.embed_url)}
              >
                <div style={{ color: "var(--neon-purple)", fontWeight: 700, fontSize: 11, textTransform: "uppercase", marginBottom: 8 }}>
                  Embedded BI
                </div>
                <div style={{ fontSize: 16, fontWeight: 700, color: "var(--text-primary)", marginBottom: 4 }}>
                  {dashboard.name || "Dashboard"}
                </div>
                <div style={{ fontSize: 13, color: "var(--text-secondary)" }}>
                  {dashboard.description || "Click to open embedded interactive dashboard"}
                </div>
              </button>
            ))}
          </div>
        </div>
      ) : (
        <div className="section" style={{ marginTop: 32 }}>
          <div className="section-title" style={{ color: "var(--accent-purple)", marginBottom: 16 }}>Embedding Setup</div>
          <div className="card premium-glass-panel" style={{ lineHeight: 1.8 }}>
            <p style={{ color: "var(--text-primary)", fontWeight: 600 }}>Metabase is available, but no public dashboard is enabled yet.</p>
            <ol style={{ paddingLeft: 20, marginTop: 12, color: "var(--text-secondary)" }}>
              <li>Open Metabase and complete the initial setup.</li>
              <li>Connect to <code className="mono">clickhouse:8123</code>.</li>
              <li>Use the readonly user <code className="mono">metabase</code> and Gold tables.</li>
              <li>Create a dashboard and enable public sharing.</li>
            </ol>
          </div>
        </div>
      )}
    </div>
  );
}
