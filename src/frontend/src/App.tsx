import { useEffect, useState } from "react";
import { Sidebar } from "./components/Layout/Sidebar";
import { OverviewPanel } from "./components/Dashboard/OverviewPanel";
import { DataQualityPanel } from "./components/Dashboard/DataQualityPanel";
import { AiConsolePanel } from "./components/Dashboard/AiConsolePanel";
import { MetabasePanel } from "./components/Dashboard/MetabasePanel";
import { GraphDatabasePanel } from "./components/Dashboard/GraphDatabasePanel";
import { AlertsPanel } from "./components/Dashboard/AlertsPanel";
import { pageFromPath, pathForPage, type Page } from "./types/navigation";
import { LoginPage, type AuthUser } from "./components/Auth/LoginPage";
import { api, getAuthToken, setAuthToken } from "./services/api.service";

const PAGE_META: Record<Page, { title: string; subtitle: string }> = {
  overview:       { title: "Overview",      subtitle: "ClickHouse Gold metrics, ingestion and service health" },
  alerts:         { title: "Alerts",        subtitle: "Operational rules for cost, reliability, freshness and data quality" },
  "data-quality": { title: "Data Quality",  subtitle: "Latest server quality checks and table-level status" },
  "ai-console":   { title: "AI Console",    subtitle: "Metadata-first monitoring chat and safe SQL console" },
  metabase:       { title: "BI Dashboards", subtitle: "Embedded Metabase analytics dashboards" },
  "graph-database": { title: "Graph Database", subtitle: "FalkorDB schema graph for QueryWeaver-style retrieval" },
};

function renderPage(page: Page) {
  switch (page) {
    case "overview": return <OverviewPanel />;
    case "alerts": return <AlertsPanel />;
    case "data-quality": return <DataQualityPanel />;
    case "ai-console": return <AiConsolePanel />;
    case "metabase": return <MetabasePanel />;
    case "graph-database": return <GraphDatabasePanel />;
    default: return <OverviewPanel />;
  }
}

export default function App() {
  const [page, setPage] = useState<Page>(() => pageFromPath(window.location.pathname));
  const [user, setUser] = useState<AuthUser | null>(null);
  const [authLoading, setAuthLoading] = useState(Boolean(getAuthToken()));
  const meta = PAGE_META[page];

  useEffect(() => {
    if (!getAuthToken()) {
      setAuthLoading(false);
      return;
    }
    api.me()
      .then((response) => setUser(response.user as unknown as AuthUser))
      .catch(() => setAuthToken(""))
      .finally(() => setAuthLoading(false));
  }, []);

  useEffect(() => {
    const onPopState = () => setPage(pageFromPath(window.location.pathname));
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  const navigate = (nextPage: Page) => {
    const path = pathForPage(nextPage);
    if (window.location.pathname !== path) window.history.pushState({}, "", path);
    setPage(nextPage);
  };

  if (authLoading) return <div className="login-page"><div className="loading">Checking session...</div></div>;
  if (!user) return <LoginPage onLogin={setUser} />;

  return (
    <div className="app-layout">
      <Sidebar
        activePage={page}
        onNavigate={navigate}
        user={user}
        onLogout={() => {
          setAuthToken("");
          setUser(null);
          navigate("overview");
        }}
      />
      <main className="main-content">
        <header className="page-header">
          <h1 className="page-title">{meta.title}</h1>
          <p className="page-subtitle">{meta.subtitle}</p>
        </header>
        <div className="page-body">
          {renderPage(page)}
        </div>
      </main>
    </div>
  );
}
