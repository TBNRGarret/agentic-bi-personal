export type Page =
  | "overview"
  | "alerts"
  | "data-quality"
  | "ai-console"
  | "metabase"
  | "graph-database";

export interface NavItem {
  id: Page;
  label: string;
  icon: string;
  section: string;
  description: string;
  path: string;
}

export const NAV_ITEMS: NavItem[] = [
  { id: "overview", label: "Overview", icon: "chart", section: "Monitoring", description: "Health and usage", path: "/Overview" },
  { id: "alerts", label: "Alerts", icon: "bell", section: "Monitoring", description: "Rules and incidents", path: "/Alerts" },
  { id: "data-quality", label: "Data Quality", icon: "check", section: "Monitoring", description: "Freshness and tests", path: "/DataQuality" },
  { id: "ai-console", label: "AI Console", icon: "terminal", section: "Agentic BI", description: "Ask and investigate", path: "/AIConsole" },
  { id: "metabase", label: "BI Dashboards", icon: "bar-chart", section: "Agentic BI", description: "Explore analytics", path: "/BIDashboards" },
  { id: "graph-database", label: "Graph Database", icon: "graph", section: "Agentic BI", description: "Inspect FalkorDB", path: "/GraphDatabase" },
];

export function pageFromPath(pathname: string): Page {
  const normalized = pathname.replace(/\/$/, "").toLowerCase();
  return NAV_ITEMS.find((item) => item.path.toLowerCase() === normalized)?.id || "overview";
}

export function pathForPage(page: Page): string {
  return NAV_ITEMS.find((item) => item.id === page)?.path || "/Overview";
}
