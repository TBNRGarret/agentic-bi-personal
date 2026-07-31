import { useState } from "react";
import { BarChart3, LayoutDashboard, MessageSquare, Terminal, Workflow } from "lucide-react";
import { ObserveChatPanel } from "./ObserveChatPanel";
import { QueryConsolePanel } from "./QueryConsolePanel";
import { GeneratedDashboardPanel } from "./GeneratedDashboardPanel";
import { MetabasePanel } from "./MetabasePanel";
import { AgentWorkflowCanvas } from "./AgentFlowGraph";

type Tab = "chat" | "sql" | "workflow" | "dashboard" | "metabase";

const TABS: Array<{
  id: Tab;
  label: string;
  icon: JSX.Element;
  render: () => JSX.Element;
}> = [
  { id: "workflow", label: "Agent Workflow Graph", icon: <Workflow size={16} />, render: () => <AgentWorkflowCanvas /> },
  { id: "chat", label: "Observe Chat", icon: <MessageSquare size={16} />, render: () => <ObserveChatPanel /> },
  { id: "sql", label: "SQL Query Console", icon: <Terminal size={16} />, render: () => <QueryConsolePanel /> },
  { id: "dashboard", label: "Generated Dashboards", icon: <LayoutDashboard size={16} />, render: () => <GeneratedDashboardPanel /> },
  { id: "metabase", label: "BI Dashboards", icon: <BarChart3 size={16} />, render: () => <MetabasePanel /> },
];

export function AiConsolePanel() {
  const [tab, setTab] = useState<Tab>("workflow");
  const activeTab = TABS.find((candidate) => candidate.id === tab) || TABS[0];

  return (
    <div className="fade-in" style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <div className="tab-bar" style={{ marginBottom: 16 }}>
        {TABS.map((item) => (
          <button
            key={item.id}
            className={`tab-btn ${tab === item.id ? "active" : ""}`}
            onClick={() => setTab(item.id)}
          >
            {item.icon}
            {item.label}
          </button>
        ))}
      </div>

      <div style={{ flex: 1, position: "relative", minHeight: 600 }}>
        <div style={{ display: "block", height: "100%" }}>{activeTab.render()}</div>
      </div>
    </div>
  );
}
