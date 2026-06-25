import { useState } from "react";
import { BarChart3, LayoutDashboard, MessageSquare, Terminal } from "lucide-react";
import { ObserveChatPanel } from "./ObserveChatPanel";
import { QueryConsolePanel } from "./QueryConsolePanel";
import { GeneratedDashboardPanel } from "./GeneratedDashboardPanel";
import { MetabasePanel } from "./MetabasePanel";

type Tab = "chat" | "sql" | "dashboard" | "metabase";

export function AiConsolePanel() {
  const [tab, setTab] = useState<Tab>("chat");

  return (
    <div className="fade-in" style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <div className="tab-bar" style={{ marginBottom: 16 }}>
        <button
          className={`tab-btn ${tab === "chat" ? "active" : ""}`}
          onClick={() => setTab("chat")}
        >
          <MessageSquare size={16} />
          Observe Chat
        </button>
        <button
          className={`tab-btn ${tab === "sql" ? "active" : ""}`}
          onClick={() => setTab("sql")}
        >
          <Terminal size={16} />
          SQL Query Console
        </button>
        <button
          className={`tab-btn ${tab === "dashboard" ? "active" : ""}`}
          onClick={() => setTab("dashboard")}
        >
          <LayoutDashboard size={16} />
          Generated Dashboards
        </button>
        <button
          className={`tab-btn ${tab === "metabase" ? "active" : ""}`}
          onClick={() => setTab("metabase")}
        >
          <BarChart3 size={16} />
          BI Dashboards
        </button>
      </div>

      <div style={{ flex: 1, position: "relative", minHeight: 600 }}>
        <div style={{ display: tab === "chat" ? "block" : "none", height: "100%" }}>
          <ObserveChatPanel />
        </div>
        <div style={{ display: tab === "sql" ? "block" : "none", height: "100%" }}>
          <QueryConsolePanel />
        </div>
        <div style={{ display: tab === "dashboard" ? "block" : "none", height: "100%" }}>
          <GeneratedDashboardPanel />
        </div>
        <div style={{ display: tab === "metabase" ? "block" : "none", height: "100%" }}>
          <MetabasePanel />
        </div>
      </div>
    </div>
  );
}
