import { useEffect, useState } from "react";
import { api } from "../../services/api.service";

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

  useEffect(() => {
    api.metabaseDashboards()
      .then((payload) => {
        const response = payload as DashboardsResponse;
        const available = response.dashboards || [];
        setDashboards(available);
        const firstEmbed = available.find((dashboard) => dashboard.embed_url)?.embed_url;
        if (firstEmbed) setActiveEmbed(firstEmbed);
        if (response.metabaseUrl) setMetabaseUrl(response.metabaseUrl);
      })
      .catch((reason) => setError((reason as Error).message))
      .finally(() => setLoading(false));
  }, []);

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
      <div className="empty-state">
        <div className="empty-state-icon">!</div>
        <div className="empty-state-text">Failed to load dashboards: {error}</div>
      </div>
    );
  }

  if (activeEmbed) {
    return (
      <div className="fade-in">
        <button className="btn btn-secondary" type="button" onClick={() => setActiveEmbed(null)}>
          Back to Dashboards
        </button>
        <iframe
          className="embed-frame"
          src={activeEmbed}
          title="Metabase Dashboard"
          style={{ border: "none", marginTop: 16 }}
        />
      </div>
    );
  }

  const embeddable = dashboards.filter((dashboard) => dashboard.embed_url);

  return (
    <div className="fade-in">
      <div className="section">
        <div className="section-title">Quick Access</div>
        <div className="metrics-grid">
          <a href={metabaseUrl} target="_blank" rel="noreferrer" className="metric-card" style={{ textDecoration: "none" }}>
            <div className="metric-icon indigo">BI</div>
            <div className="metric-value" style={{ fontSize: 16 }}>Open Metabase</div>
            <div className="metric-label">Full BI Platform</div>
          </a>
          <a href={`${metabaseUrl}/collection/root`} target="_blank" rel="noreferrer" className="metric-card" style={{ textDecoration: "none" }}>
            <div className="metric-icon cyan">ALL</div>
            <div className="metric-value" style={{ fontSize: 16 }}>Collections</div>
            <div className="metric-label">Browse Dashboards</div>
          </a>
          <a href={`${metabaseUrl}/question#new`} target="_blank" rel="noreferrer" className="metric-card" style={{ textDecoration: "none" }}>
            <div className="metric-icon emerald">NEW</div>
            <div className="metric-value" style={{ fontSize: 16 }}>New Question</div>
            <div className="metric-label">Create Query</div>
          </a>
        </div>
      </div>

      {embeddable.length > 0 ? (
        <div className="section">
          <div className="section-title">Public Dashboards</div>
          <div className="metrics-grid">
            {embeddable.map((dashboard, index) => (
              <button
                key={dashboard.id || index}
                type="button"
                className="metric-card"
                style={{ cursor: "pointer", textAlign: "left" }}
                onClick={() => dashboard.embed_url && setActiveEmbed(dashboard.embed_url)}
              >
                <div className="metric-icon purple">BI</div>
                <div className="metric-value" style={{ fontSize: 14 }}>
                  {dashboard.name || "Dashboard"}
                </div>
                <div className="metric-label">
                  {dashboard.description || "Open embedded dashboard"}
                </div>
              </button>
            ))}
          </div>
        </div>
      ) : (
        <div className="section">
          <div className="section-title">Embedding Setup</div>
          <div className="card" style={{ lineHeight: 1.8 }}>
            <p>Metabase is available, but no public dashboard is enabled yet.</p>
            <ol style={{ paddingLeft: 20 }}>
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
