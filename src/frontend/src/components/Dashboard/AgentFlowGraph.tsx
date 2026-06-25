import { useState, useRef } from "react";
import {
  Database, Sparkles, User, Zap, CheckCircle2, AlertCircle, Loader2,
  Workflow, Layers, Server, Activity, ShieldAlert, Cpu,
  BarChart3, PieChart, Info, GitCommit, GitMerge, ArrowRight,
  CornerDownRight, Terminal, X, FileCode
} from "lucide-react";

interface AgentFlowGraphProps {
  stages: any[];
  data?: any;
  loading?: boolean;
  question?: string;
}

interface FlowNode {
  id: string;
  label: string;
  sublabel?: string;
  type: "user" | "planner" | "agent" | "datasource" | "result" | "error";
  active?: boolean;
}

interface WorkflowGraphNode {
  id?: string;
  label?: string;
  sublabel?: string;
  kind?: string;
  c4Level?: string;
  layer?: string;
  status?: string;
}

interface WorkflowGraphEdge {
  from?: string;
  to?: string;
  label?: string;
}

interface WorkflowGraph {
  title?: string;
  notation?: string;
  nodes?: WorkflowGraphNode[];
  edges?: WorkflowGraphEdge[];
}

function buildFlowNodes(
  stages: any[],
  data?: any,
  loading?: boolean,
  question?: string,
  workflowGraph?: WorkflowGraph,
): FlowNode[] {
  const activeQuestion = question || data?.question || "";
  const activeIntent = data?.intent || "";
  const activeAgentName = (data?.agent || "").toLowerCase();
  const intentStr = (activeIntent + " " + activeQuestion).toLowerCase();
  const stageStr = stages.map(s => (s.stage || s.message || "")).join(" ").toLowerCase();
  const selectedTables = Array.isArray(data?.selectedTables) ? data.selectedTables : [];

  if (workflowGraph?.nodes?.length) {
    // Filter and tailor nodes for HLD explainability
    const filtered = workflowGraph.nodes.filter((node) => {
      const lbl = (node.label || "").toLowerCase();
      const kind = (node.kind || "").toLowerCase();

      // 1. Always include User / Context
      if (kind === "person" || lbl.includes("client") || lbl.includes("user")) return true;

      // 2. Always include API / Orchestrator / Guardrail / Planner
      if (lbl.includes("service") || lbl.includes("guardrail") || lbl.includes("planner") || lbl.includes("orchestrator")) return true;

      // 3. Metadata Catalog (DataHub)
      if (lbl.includes("datahub") || lbl.includes("metadata")) return true;

      // 4. Specialist Agents: ONLY include the specific agent active for this query
      if (lbl.includes("agent") && !lbl.includes("response") && !lbl.includes("summarizer")) {
        if (activeAgentName && lbl.includes(activeAgentName.replace(/agent/g, "").trim())) return true;
        if (lbl.includes("insight") && (intentStr.includes("root-cause") || intentStr.includes("latency") || intentStr.includes("error") || stageStr.includes("insight"))) return true;
        if (lbl.includes("qa") && (intentStr.includes("failed") || intentStr.includes("batch") || stageStr.includes("qa") || stageStr.includes("audit"))) return true;
        if (lbl.includes("partition") && (intentStr.includes("partition") || intentStr.includes("phân vùng") || stageStr.includes("partition"))) return true;
        if (lbl.includes("forecast") && (intentStr.includes("forecast") || intentStr.includes("dự báo") || stageStr.includes("forecast"))) return true;
        if (lbl.includes("sql") || lbl.includes("chart")) {
          if (activeAgentName.includes("sql") || activeAgentName.includes("chart") || intentStr.includes("tong quan") || intentStr.includes("biểu đồ") || stageStr.includes("sql") || (!intentStr.includes("root-cause") && !intentStr.includes("failed"))) return true;
        }
        return false;
      }

      // 5. Background data pipeline (MinIO, Airflow, dbt, Source export): exclude unless explicitly asked about
      if (lbl.includes("source export") || lbl.includes("minio") || lbl.includes("airflow") || lbl.includes("dbt") || lbl.includes("clickhouse raw") || lbl.includes("batch fail audit")) {
        if (intentStr.includes("airflow") || intentStr.includes("dbt") || intentStr.includes("minio") || intentStr.includes("pipeline") || intentStr.includes("raw")) return true;
        return false;
      }

      // 6. Target Database (ClickHouse analytics / OLAP)
      if (lbl.includes("clickhouse") || lbl.includes("database") || lbl.includes("analytics")) return true;

      // 7. Response / Result
      if (lbl.includes("response") || lbl.includes("result") || lbl.includes("summarizer")) return true;

      return false;
    });

    return filtered.map((node, index) => {
      let sublabel = graphSublabel(node);
      const lbl = (node.label || "").toLowerCase();
      const u = data?.llmUsage || data?.usage || data?.llmSynthesis?.usage || {};
      const tokens = (u?.totalTokens ?? u?.total_tokens) ? Number(u?.totalTokens ?? u?.total_tokens) : 0;

      // Dynamically inject exact query explainability metadata into sublabels
      if (node.kind === "person" || lbl.includes("client")) {
        sublabel = activeQuestion ? `Câu hỏi: ${activeQuestion.length > 45 ? activeQuestion.slice(0, 42) + "…" : activeQuestion}` : sublabel;
      } else if (lbl.includes("guardrail") || lbl.includes("planner") || lbl.includes("orchestrator")) {
        const tokenStr = tokens > 0 ? ` (${Math.round(tokens * 0.25).toLocaleString()} tokens)` : "";
        sublabel = activeIntent ? `Intent: ${activeIntent}${tokenStr}` : (sublabel ? sublabel + tokenStr : tokenStr);
      } else if (lbl.includes("datahub") || lbl.includes("metadata")) {
        sublabel = selectedTables.length > 0 ? `Bảng: ${selectedTables.join(", ")}` : "Schema & Lineage lookup";
      } else if (lbl.includes("clickhouse") || lbl.includes("database") || lbl.includes("analytics")) {
        sublabel = data?.target ? `Nguồn đích: ${data.target}` : "ClickHouse OLAP Tables";
      } else if (lbl.includes("response") || lbl.includes("result") || lbl.includes("summarizer")) {
        const parts: string[] = [];
        if (data?.rowCount !== undefined) parts.push(`${data.rowCount} rows`);
        if (data?.executionTimeMs !== undefined) parts.push(`${data.executionTimeMs} ms`);
        if (tokens > 0) parts.push(`${Math.round(tokens * 0.25).toLocaleString()} tokens`);
        if (parts.length) sublabel = parts.join(" · ");
      } else if (lbl.includes("agent")) {
        const tokenStr = tokens > 0 ? ` (${Math.round(tokens * 0.5).toLocaleString()} tokens)` : "";
        if (sublabel) sublabel += tokenStr;
        else if (tokenStr) sublabel = tokenStr.trim();
      }

      return {
        id: node.id || "workflow-" + index,
        label: node.label || formatStageName(node.kind || "workflow"),
        sublabel,
        type: graphNodeType(node),
        active: Boolean(loading && index === filtered.length - 1),
      };
    });
  }

  const nodes: FlowNode[] = [];

  // 1. User query node
  if (question || (stages.length > 0) || data) {
    nodes.push({
      id: "user",
      label: "User Query",
      sublabel: question ? (question.length > 32 ? `${question.slice(0, 30)}…` : question) : undefined,
      type: "user",
    });
  }

  // 2. From stages — extract planner, agent steps
  const seenStages = new Set<string>();
  for (const stage of stages) {
    const name = stage.stage || stage.message || "";
    if (!name || seenStages.has(name)) continue;
    seenStages.add(name);

    const u = data?.llmUsage || data?.usage || data?.llmSynthesis?.usage || {};
    const tokens = (u?.totalTokens ?? u?.total_tokens) ? Number(u?.totalTokens ?? u?.total_tokens) : 0;
    const lower = name.toLowerCase();
    if (lower.includes("plan") || lower.includes("rout") || lower.includes("intent")) {
      nodes.push({
        id: `stage-${nodes.length}`,
        label: "Planner",
        sublabel: (stage.intent || name) + (tokens > 0 ? ` (${Math.round(tokens * 0.25).toLocaleString()} tokens)` : ""),
        type: "planner",
      });
    } else if (lower.includes("sql") || lower.includes("query") || lower.includes("generat")) {
      nodes.push({
        id: `stage-${nodes.length}`,
        label: "SQL Agent",
        sublabel: (stage.datasource || name) + (tokens > 0 ? ` (${Math.round(tokens * 0.5).toLocaleString()} tokens)` : ""),
        type: "agent",
      });
    } else if (lower.includes("forecast") || lower.includes("predict")) {
      nodes.push({
        id: `stage-${nodes.length}`,
        label: "Forecast Agent",
        sublabel: name + (tokens > 0 ? ` (${Math.round(tokens * 0.5).toLocaleString()} tokens)` : ""),
        type: "agent",
      });
    } else if (lower.includes("partition")) {
      nodes.push({
        id: `stage-${nodes.length}`,
        label: "Partition Agent",
        sublabel: name + (tokens > 0 ? ` (${Math.round(tokens * 0.5).toLocaleString()} tokens)` : ""),
        type: "agent",
      });
    } else if (lower.includes("monitor") || lower.includes("observe")) {
      nodes.push({
        id: `stage-${nodes.length}`,
        label: "Monitor Agent",
        sublabel: name + (tokens > 0 ? ` (${Math.round(tokens * 0.5).toLocaleString()} tokens)` : ""),
        type: "agent",
      });
    } else if (lower.includes("execut") || lower.includes("run")) {
      nodes.push({
        id: `stage-${nodes.length}`,
        label: "Executor",
        sublabel: name + (tokens > 0 ? ` (${Math.round(tokens * 0.25).toLocaleString()} tokens)` : ""),
        type: "agent",
      });
    } else {
      nodes.push({
        id: `stage-${nodes.length}`,
        label: formatStageName(name),
        sublabel: tokens > 0 ? `${Math.round(tokens * 0.25).toLocaleString()} tokens` : undefined,
        type: "planner",
      });
    }
  }

  // 3. If we have final data — add datasource & result
  if (data) {
    const target = typeof data.target === "string" ? data.target : undefined;
    const datasource = typeof data.datasource === "string" ? data.datasource : target;
    const selectedTables = Array.isArray(data.selectedTables) ? data.selectedTables : [];
    const agent = typeof data.agent === "string" ? data.agent : undefined;
    const intent = typeof data.intent === "string" ? data.intent : undefined;
    const status = typeof data.status === "string" ? data.status : undefined;

    // If no stages were captured, add planner/agent from final data
    if (nodes.length <= 1) {
      const u = data?.llmUsage || data?.usage || data?.llmSynthesis?.usage || {};
      const tokens = (u?.totalTokens ?? u?.total_tokens) ? Number(u?.totalTokens ?? u?.total_tokens) : 0;
      if (intent) {
        nodes.push({
          id: "planner-final",
          label: "Planner",
          sublabel: intent + (tokens > 0 ? ` (${Math.round(tokens * 0.3).toLocaleString()} tokens)` : ""),
          type: "planner",
        });
      }
      if (agent) {
        nodes.push({
          id: "agent-final",
          label: formatStageName(agent),
          sublabel: tokens > 0 ? `${Math.round(tokens * 0.7).toLocaleString()} tokens` : undefined,
          type: "agent",
        });
      }
    }

    // Datasource node
    if (datasource || selectedTables.length > 0) {
      nodes.push({
        id: "datasource",
        label: datasource || "Database",
        sublabel: selectedTables.length > 0 ? selectedTables.slice(0, 2).join(", ") : undefined,
        type: "datasource",
      });
    }

    // Result node
    const rowCount = typeof data.rowCount === "number" ? data.rowCount : undefined;
    const execMs = typeof data.executionTimeMs === "number" ? data.executionTimeMs : undefined;

    if (status === "error") {
      nodes.push({
        id: "result",
        label: "Error",
        sublabel: "Query failed",
        type: "error",
      });
    } else {
      const parts: string[] = [];
      if (rowCount !== undefined) parts.push(`${rowCount} rows`);
      if (execMs !== undefined) parts.push(`${execMs}ms`);
      nodes.push({
        id: "result",
        label: "Result",
        sublabel: parts.length ? parts.join(" · ") : "Complete",
        type: "result",
      });
    }
  }

  // If loading and last node exists, mark it active
  if (loading && nodes.length > 0) {
    nodes[nodes.length - 1].active = true;
  }

  return nodes;
}

function getWorkflowGraph(data?: any): WorkflowGraph | undefined {
  const graph = data?.workflowGraph;
  if (!graph || typeof graph !== "object" || !Array.isArray(graph.nodes)) {
    return undefined;
  }
  return graph as WorkflowGraph;
}

function graphNodeType(node: WorkflowGraphNode): FlowNode["type"] {
  const status = String(node.status || "").toLowerCase();
  const kind = String(node.kind || "").toLowerCase();
  if (status === "error" || kind === "error") return "error";
  if (kind === "person") return "user";
  if (["database", "storage", "datahub", "source"].includes(kind)) return "datasource";
  if (kind === "clarification") return "planner";
  if (kind === "result") return "result";
  if (["planner", "guardrail", "service", "airflow", "transform"].includes(kind)) return "planner";
  return "agent";
}

function graphSublabel(node: WorkflowGraphNode): string | undefined {
  const meta = [node.layer, node.c4Level].filter(Boolean).join(" / ");
  const text = String(node.sublabel || "").trim();
  if (meta && text) return meta + " - " + text;
  return meta || text || undefined;
}

function graphEdgeLabel(graph: WorkflowGraph | undefined, from: string, to: string): string | undefined {
  return graph?.edges?.find((edge) => edge.from === from && edge.to === to)?.label;
}

function formatStageName(name: string): string {
  return name
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase())
    .slice(0, 28);
}

const NODE_COLORS: Record<
  FlowNode["type"],
  { bg: string; border: string; text: string; icon: string; gradient: string }
> = {
  user: {
    bg: "#fef3e2",
    border: "#f97316",
    text: "#9a3412",
    icon: "#f97316",
    gradient: "linear-gradient(135deg, #fef3e2 0%, #fff7ed 100%)",
  },
  planner: {
    bg: "#eef2ff",
    border: "#6366f1",
    text: "#3730a3",
    icon: "#6366f1",
    gradient: "linear-gradient(135deg, #eef2ff 0%, #e0e7ff 100%)",
  },
  agent: {
    bg: "#ecfeff",
    border: "#06b6d4",
    text: "#155e75",
    icon: "#06b6d4",
    gradient: "linear-gradient(135deg, #ecfeff 0%, #cffafe 100%)",
  },
  datasource: {
    bg: "#f0fdf4",
    border: "#22c55e",
    text: "#166534",
    icon: "#22c55e",
    gradient: "linear-gradient(135deg, #f0fdf4 0%, #dcfce7 100%)",
  },
  result: {
    bg: "#f0fdf4",
    border: "#10b981",
    text: "#065f46",
    icon: "#10b981",
    gradient: "linear-gradient(135deg, #f0fdf4 0%, #d1fae5 100%)",
  },
  error: {
    bg: "#fef2f2",
    border: "#ef4444",
    text: "#991b1b",
    icon: "#ef4444",
    gradient: "linear-gradient(135deg, #fef2f2 0%, #fee2e2 100%)",
  },
};

function getConnectorLabel(prevNode: FlowNode, currNode: FlowNode, graph?: WorkflowGraph): string {
  if (prevNode.type === "user") return "gửi yêu cầu truy vấn";
  if (prevNode.type === "planner") {
    if (currNode.type === "agent") return `điều phối tới ${currNode.label}`;
    if (currNode.type === "datasource" || currNode.label.includes("DataHub")) return "tra cứu metadata & schema";
    return "kiểm tra guardrail & kế hoạch";
  }
  if (prevNode.type === "agent") {
    if (currNode.type === "datasource" || currNode.label.includes("ClickHouse")) return "truy vấn dữ liệu OLAP";
    if (currNode.type === "result" || currNode.type === "error") return "tổng hợp & trả kết quả";
    return "chuyển tiếp dữ liệu";
  }
  if (prevNode.type === "datasource" || prevNode.label.includes("DataHub")) {
    if (currNode.type === "agent") return "cung cấp schema cho Agent";
    if (currNode.type === "result") return "trả dữ liệu kết xuất";
    return "truy xuất dữ liệu";
  }
  if (currNode.type === "result") return "hiển thị kết quả";
  if (currNode.type === "error") return "dừng thực thi do lỗi";
  return "kết nối luồng dữ liệu";
}

function NodeIcon({ type, size = 14 }: { type: FlowNode["type"]; size?: number }) {
  const color = NODE_COLORS[type].icon;
  switch (type) {
    case "user": return <User size={size} color={color} />;
    case "planner": return <Zap size={size} color={color} />;
    case "agent": return <Sparkles size={size} color={color} />;
    case "datasource": return <Database size={size} color={color} />;
    case "result": return <CheckCircle2 size={size} color={color} />;
    case "error": return <AlertCircle size={size} color={color} />;
  }
}

export function AgentFlowGraph({ stages, data, loading, question }: AgentFlowGraphProps) {
  const workflowGraph = getWorkflowGraph(data);
  const nodes = buildFlowNodes(stages || [], data, loading, question, workflowGraph);

  if (nodes.length === 0) {
    return (
      <div className="agent-flow-panel">
        <div className="agent-flow-header">
          <Zap size={14} />
          <span>Agent Flow</span>
        </div>
        <div className="agent-flow-empty">
          <Zap size={24} />
          <span>Agent flow will appear here when you send a query</span>
        </div>
      </div>
    );
  }

  return (
    <div className="agent-flow-panel">
      <div className="agent-flow-header">
        <Zap size={14} />
        <span>{workflowGraph?.notation ? "Agent Flow - " + workflowGraph.notation : "Agent Flow"}</span>
        {loading && <Loader2 size={14} className="agent-flow-spinner" />}
      </div>
      <div className="agent-flow-nodes">
        {nodes.map((node, index) => {
          const colors = NODE_COLORS[node.type];
          return (
            <div key={node.id}>
              {/* Connector line with Arrow and Label */}
              {index > 0 && (!workflowGraph || graphEdgeLabel(workflowGraph, nodes[index - 1].id, node.id)) && (
                <div className="agent-flow-connector">
                  <svg width="100%" height="36" style={{ overflow: "visible" }}>
                    <defs>
                      <marker
                        id={`arrow-${index}`}
                        viewBox="0 0 10 10"
                        refX="6"
                        refY="5"
                        markerWidth="5"
                        markerHeight="5"
                        orient="auto-start-reverse"
                      >
                        <path
                          d="M 0 1.5 L 6 5 L 0 8.5 z"
                          fill={loading && index === nodes.length - 1 ? "#cbd5e1" : "#9ca3af"}
                        />
                      </marker>
                    </defs>
                    <line
                      x1="50%"
                      y1="0"
                      x2="50%"
                      y2="28"
                      stroke={loading && index === nodes.length - 1 ? "#cbd5e1" : "#9ca3af"}
                      strokeWidth="1.5"
                      strokeDasharray={loading && index === nodes.length - 1 ? "4 3" : "none"}
                      markerEnd={`url(#arrow-${index})`}
                    >
                      {loading && index === nodes.length - 1 && (
                        <animate
                          attributeName="stroke-dashoffset"
                          values="7;0"
                          dur="0.6s"
                          repeatCount="indefinite"
                        />
                      )}
                    </line>
                    <text
                      x="50%"
                      dx="10"
                      y="20"
                      fontSize="9.5"
                      fill="#9ca3af"
                      fontWeight="600"
                      style={{ letterSpacing: "0.02em" }}
                    >
                      {getConnectorLabel(nodes[index - 1], node, workflowGraph)}
                    </text>
                  </svg>
                </div>
              )}
              {/* Node */}
              <div
                className={`agent-flow-node ${node.active ? "active" : ""}`}
                style={{
                  background: colors.gradient,
                  borderColor: colors.border,
                }}
              >
                <div className="agent-flow-node-icon">
                  <NodeIcon type={node.type} />
                </div>
                <div className="agent-flow-node-content">
                  <div className="agent-flow-node-label" style={{ color: colors.text }}>
                    {node.label}
                  </div>
                  {node.sublabel && (
                    <div className="agent-flow-node-sublabel">
                      {node.sublabel}
                    </div>
                  )}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ==========================================================================
   Agent Workflow Canvas — High-Level Design Interactive C4 Visualization
   ========================================================================== */

interface C4NodeDef {
  id: string;
  label: string;
  sublabel: string;
  desc: string;
  c4Level: string;
  layer: string;
  kind: string;
  status: "ready" | "warning" | "error";
  accent: string;
  iconName: string;
  tags: string[];
  codeFile?: string;
  connections?: string[];
  contract?: {
    input?: string;
    output?: string;
    description?: string;
  };
}

const HIGH_LEVEL_NODES: C4NodeDef[] = [
  {
    id: "user",
    label: "Web UI Client",
    sublabel: "React + Vite SPA",
    desc: "Người dùng đặt câu hỏi tiếng Việt tự nhiên về BI hoặc theo dõi sức khỏe hệ thống (observability).",
    c4Level: "Person",
    layer: "C1 Context",
    kind: "person",
    status: "ready",
    accent: "#f97316",
    iconName: "User",
    tags: ["Vietnamese NL", "Dashboard UI", "REST / SSE"],
    codeFile: "src/frontend/src/App.tsx",
    connections: ["FastAPI Router & Context"],
  },
  {
    id: "router",
    label: "FastAPI Router & Context",
    sublabel: "/api/agent/chat",
    desc: "Tiếp nhận HTTP request, quản lý session history và structured memory qua ContextManager.",
    c4Level: "Container",
    layer: "API Gateway",
    kind: "service",
    status: "ready",
    accent: "#6366f1",
    iconName: "Terminal",
    tags: ["FastAPI", "Session Memory", "Postgres Audit"],
    codeFile: "src/backend/app/routers/query.py",
    connections: ["Multi-Agent Orchestrator", "Session Memory"],
  },
  {
    id: "orchestrator",
    label: "Multi-Agent Orchestrator",
    sublabel: "Control-Plane Agent",
    desc: "Trích xuất semantic slots (intent, domain, metric, layer). Kiểm tra guardrail và điều phối các specialist agents.",
    c4Level: "Component",
    layer: "Orchestration",
    kind: "planner",
    status: "ready",
    accent: "#8b5cf6",
    iconName: "Workflow",
    tags: ["Control Plane", "Semantic Slots", "Clarification Gate"],
    codeFile: "src/backend/app/agents/multi_agent.py",
    connections: ["DataHub Table Selector", "Chart Agent", "Insight Agent", "Forecast Agent", "QA Agent", "Partition Agent", "Summarizer Agent"],
    contract: {
      input: "Natural Language Question + Active Session Context",
      output: "Deterministic Agent Plan + Specialist Agent Responses",
      description: "Điều phối song song các specialist agents qua asyncio.gather và quản lý luồng thực thi chính.",
    },
  },
  {
    id: "table_selector",
    label: "DataHub Table Selector",
    sublabel: "Metadata Ranking",
    desc: "Truy xuất DataHub GraphQL API để chọn bảng phù hợp (gold > silver) bằng keyword & embedding ranking.",
    c4Level: "Component",
    layer: "Metadata",
    kind: "datasource",
    status: "ready",
    accent: "#06b6d4",
    iconName: "Database",
    tags: ["DataHub GMS", "Schema Discovery", "Table Ranking"],
    codeFile: "src/backend/app/agents/table_selector.py",
    connections: ["Chart Agent", "ClickHouse OLAP"],
  },
  {
    id: "chart_agent",
    label: "Chart Agent (SQL Specialist)",
    sublabel: "NL2SQL + Chart Spec",
    desc: "Sinh SQL an toàn cho ClickHouse, kiểm tra read-only guardrail và trả về dữ liệu kèm biểu đồ (bar, line, pie).",
    c4Level: "Component",
    layer: "Specialist Agents",
    kind: "agent",
    status: "ready",
    accent: "#10b981",
    iconName: "BarChart3",
    tags: ["NL2SQL", "ClickHouse SQL", "Dynamic Chart"],
    codeFile: "src/backend/app/agents/chart_spec.py",
    connections: ["ClickHouse OLAP", "Summarizer Agent"],
    contract: {
      input: "Question + Selected Tables Schema + Semantic Slots",
      output: "Generated SQL + Result Rows + Visual Chart Spec",
      description: "Đảm bảo SQL an toàn (chặn DDL/DML) và áp dụng Row Security theo principal.",
    },
  },
  {
    id: "insight_agent",
    label: "Insight Agent",
    sublabel: "Root-Cause Analysis",
    desc: "Phân tích nguyên nhân gốc rễ (root-cause) cho latency, errors, so sánh đa chiều giữa các services và models.",
    c4Level: "Component",
    layer: "Specialist Agents",
    kind: "agent",
    status: "ready",
    accent: "#10b981",
    iconName: "Activity",
    tags: ["Anomaly Detection", "Telemetry Analysis", "Root Cause"],
    codeFile: "src/backend/app/agents/monitoring_agent.py",
    connections: ["ClickHouse OLAP", "Summarizer Agent"],
  },
  {
    id: "forecast_agent",
    label: "Forecast Agent",
    sublabel: "Time-Series Prediction",
    desc: "Dự báo xu hướng từ dữ liệu lịch sử bằng Simple Exponential Smoothing hoặc mô hình dự báo LLM-based.",
    c4Level: "Component",
    layer: "Specialist Agents",
    kind: "agent",
    status: "ready",
    accent: "#10b981",
    iconName: "PieChart",
    tags: ["Trend Forecasting", "Exponential Smoothing", "Predictive BI"],
    codeFile: "src/backend/app/agents/multi_agent.py",
    connections: ["ClickHouse OLAP", "Summarizer Agent"],
  },
  {
    id: "qa_agent",
    label: "QA Agent",
    sublabel: "Ingestion & Audit Health",
    desc: "Kiểm tra chất lượng dữ liệu, theo dõi các lô dữ liệu (batches) bị lỗi hoặc bị cách ly (quarantined) trong quá trình ingest.",
    c4Level: "Component",
    layer: "Specialist Agents",
    kind: "agent",
    status: "ready",
    accent: "#10b981",
    iconName: "ShieldAlert",
    tags: ["Data Quality Gate", "Batch Quarantine", "Pipeline Audit"],
    codeFile: "src/backend/app/agents/monitoring_agent.py",
    connections: ["ClickHouse OLAP", "Airflow Orchestration", "Summarizer Agent"],
  },
  {
    id: "partition_agent",
    label: "Partition Agent",
    sublabel: "Partition Strategy Analysis",
    desc: "Tự động phân tích chiến lược phân vùng, kiểm tra partition keys (daily/monthly), phát hiện skew và partition rỗng qua system.parts.",
    c4Level: "Component",
    layer: "Specialist Agents",
    kind: "partition",
    status: "ready",
    accent: "#f59e0b",
    iconName: "Layers",
    tags: ["Partition Pruning", "ClickHouse Parts", "Skew Detection"],
    codeFile: "src/backend/app/agents/partition_agent.py",
    connections: ["ClickHouse OLAP", "Summarizer Agent"],
    contract: {
      input: "Target Tables + Time Range Slot",
      output: "PartitionHealthReport + Pruning Recommendations",
      description: "Tương tác trực tiếp với PartitionManager để tối ưu hóa truy vấn OLAP quy status lớn.",
    },
  },
  {
    id: "minio",
    label: "MinIO Object Storage",
    sublabel: "Raw Landing Zone",
    desc: "Lưu trữ các file raw JSONL và manifests từ nguồn xuất. Được Airflow streaming đọc trực tiếp không cần tải về local.",
    c4Level: "Container",
    layer: "Data Pipeline",
    kind: "storage",
    status: "ready",
    accent: "#3b82f6",
    iconName: "Server",
    tags: ["S3 Streaming", "Raw JSONL", "Manifest Store"],
    codeFile: "dags/agentic_bi_pipeline.py",
    connections: ["Airflow Orchestration"],
  },
  {
    id: "airflow",
    label: "Airflow Orchestration",
    sublabel: "Ingestion & Transform DAGs",
    desc: "Quản lý 2 DAG độc lập: sync_external_staging_to_dwh (chạy mỗi 30 phút, quarantine lỗi) và transform_raw_to_gold (dbt build).",
    c4Level: "Container",
    layer: "Data Pipeline",
    kind: "airflow",
    status: "ready",
    accent: "#ec4899",
    iconName: "Cpu",
    tags: ["Decoupled DAGs", "Continuous Ingest", "TriggerDagRun"],
    codeFile: "dags/agentic_bi_pipeline.py",
    connections: ["ClickHouse OLAP", "MinIO Object Storage"],
  },
  {
    id: "clickhouse",
    label: "ClickHouse OLAP",
    sublabel: "Analytics & Marts",
    desc: "Hệ quản trị cơ sở dữ liệu chính cho Agentic BI, lưu trữ vsf_raw, vsf_stg, vsf_mart và bảng quản trị lô import.",
    c4Level: "Data",
    layer: "Database",
    kind: "database",
    status: "ready",
    accent: "#ef4444",
    iconName: "Database",
    tags: ["OLAP Columnar", "Gold Analytics", "System Parts"],
    codeFile: "src/backend/app/connectors/clickhouse.py",
    connections: ["Summarizer Agent", "Specialist Agents"],
  },
  {
    id: "summarizer",
    label: "Summarizer Agent",
    sublabel: "Synthesizer & Response",
    desc: "Tổng hợp kết quả từ các specialist agents, kết hợp bảng dữ liệu, biểu đồ và sinh câu trả lời tự nhiên hoàn chỉnh.",
    c4Level: "Component",
    layer: "Synthesis",
    kind: "result",
    status: "ready",
    accent: "#10b981",
    iconName: "Sparkles",
    tags: ["LLM Synthesis", "Final Grounded Answer", "Markdown UI"],
    codeFile: "src/backend/app/agents/multi_agent.py",
    connections: ["Web UI Client"],
  },
];

function CanvasNodeIcon({ name, accent }: { name: string; accent: string }) {
  switch (name) {
    case "User": return <User size={22} color={accent} />;
    case "Terminal": return <Terminal size={22} color={accent} />;
    case "Workflow": return <Workflow size={22} color={accent} />;
    case "Database": return <Database size={22} color={accent} />;
    case "BarChart3": return <BarChart3 size={22} color={accent} />;
    case "Activity": return <Activity size={22} color={accent} />;
    case "PieChart": return <PieChart size={22} color={accent} />;
    case "ShieldAlert": return <ShieldAlert size={22} color={accent} />;
    case "Layers": return <Layers size={22} color={accent} />;
    case "Server": return <Server size={22} color={accent} />;
    case "Cpu": return <Cpu size={22} color={accent} />;
    case "Sparkles": return <Sparkles size={22} color={accent} />;
    default: return <Workflow size={22} color={accent} />;
  }
}

function getLayerConnectionLabel(fromLayer: string, toLayer: string): string {
  if (fromLayer === "C1 Context" && toLayer === "API Gateway") return "Gửi yêu cầu truy vấn tự nhiên (REST / SSE Stream)";
  if (fromLayer === "API Gateway" && toLayer === "Orchestration") return "Chuyển giao Context & Quản lý bộ nhớ phiên (Session Memory)";
  if (fromLayer === "Orchestration" && toLayer === "Metadata") return "Truy xuất siêu dữ liệu & Xếp hạng bảng (DataHub GMS)";
  if (fromLayer === "Orchestration" && toLayer === "Specialist Agents") return "Điều phối & Giao việc song song (asyncio.gather)";
  if (fromLayer === "Metadata" && toLayer === "Specialist Agents") return "Cung cấp Schema & Context cho Specialist Agents";
  if (fromLayer === "Specialist Agents" && toLayer === "Data Pipeline") return "Tham chiếu luồng Ingestion & Kiểm tra trạng thái lô";
  if (fromLayer === "Specialist Agents" && toLayer === "Database") return "Sinh SQL an toàn & Truy vấn dữ liệu phân vùng";
  if (fromLayer === "Data Pipeline" && toLayer === "Database") return "Lưu trữ & Chuẩn hóa dữ liệu (Airflow & dbt Quality Gate)";
  if (fromLayer === "Database" && toLayer === "Synthesis") return "Kết xuất dữ liệu thô & Biểu đồ hóa (Synthesizer)";
  return `Điều phối & Luồng dữ liệu (${fromLayer} → ${toLayer})`;
}

export function AgentWorkflowCanvas() {
  const [selectedId, setSelectedId] = useState<string>("orchestrator");
  const [viewMode, setViewMode] = useState<"all" | "agents" | "pipeline">("all");
  const [inspectorWidth, setInspectorWidth] = useState<number>(400);
  const isDraggingRef = useRef(false);

  const handleMouseDown = (e: React.MouseEvent) => {
    e.preventDefault();
    isDraggingRef.current = true;
    document.addEventListener("mousemove", handleMouseMove);
    document.addEventListener("mouseup", handleMouseUp);
  };

  const handleMouseMove = (e: MouseEvent) => {
    if (!isDraggingRef.current) return;
    const newWidth = window.innerWidth - e.clientX;
    if (newWidth > 280 && newWidth < 800) {
      setInspectorWidth(newWidth);
    }
  };

  const handleMouseUp = () => {
    isDraggingRef.current = false;
    document.removeEventListener("mousemove", handleMouseMove);
    document.removeEventListener("mouseup", handleMouseUp);
  };

  const selectedNode = HIGH_LEVEL_NODES.find((node) => node.id === selectedId) || HIGH_LEVEL_NODES[2];

  const filteredNodes = HIGH_LEVEL_NODES.filter((node) => {
    if (viewMode === "agents") {
      return ["Orchestration", "Specialist Agents", "Synthesis"].includes(node.layer);
    }
    if (viewMode === "pipeline") {
      return ["Data Pipeline", "Database", "Metadata"].includes(node.layer);
    }
    return true;
  });

  const layers = Array.from(new Set(filteredNodes.map((node) => node.layer)));

  return (
    <div className="workflow-canvas-container fade-in" style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}>
      <div className="workflow-canvas-header" style={{ flexShrink: 0 }}>
        <div className="workflow-canvas-title-group">
          <div className="workflow-canvas-title">
            <Workflow size={24} />
            <span>Agent Workflow & C4 Architecture</span>
          </div>
          <div className="workflow-canvas-subtitle">
            Mô hình thiết kế High-Level Design: Điều phối Multi-Agent, Phân vùng ClickHouse & Luồng Airflow
          </div>
        </div>
        <div className="workflow-canvas-toolbar">
          <button
            type="button"
            className={`workflow-mode-btn ${viewMode === "all" ? "active" : ""}`}
            onClick={() => setViewMode("all")}
          >
            <Layers size={15} />
            <span>Toàn bộ hệ thống</span>
          </button>
          <button
            type="button"
            className={`workflow-mode-btn ${viewMode === "agents" ? "active" : ""}`}
            onClick={() => setViewMode("agents")}
          >
            <Workflow size={15} />
            <span>Luồng Agents</span>
          </button>
          <button
            type="button"
            className={`workflow-mode-btn ${viewMode === "pipeline" ? "active" : ""}`}
            onClick={() => setViewMode("pipeline")}
          >
            <Server size={15} />
            <span>Luồng Dữ Liệu</span>
          </button>
        </div>
      </div>

      <div className="workflow-canvas-body" style={{ display: "flex", flexDirection: "row", flex: 1, minHeight: 0, width: "100%", overflow: "hidden", position: "relative" }}>
        <div className="workflow-grid" style={{ 
          flex: 1, 
          minWidth: 0, 
          overflowY: "auto", 
          padding: "36px 48px", 
          paddingRight: selectedNode ? "36px" : "48px",
          backgroundImage: "radial-gradient(rgba(255, 255, 255, 0.07) 1px, transparent 1px)",
          backgroundSize: "24px 24px",
          backgroundColor: "#0b0f19",
          display: "flex",
          flexDirection: "column",
          gap: "16px"
        }}>
          {layers.map((layer, layerIndex) => {
            const nodesInLayer = filteredNodes.filter((node) => node.layer === layer);
            const nextLayerNodes = layerIndex < layers.length - 1 ? filteredNodes.filter((node) => node.layer === layers[layerIndex + 1]) : [];
            return (
              <div key={layer} className="workflow-layer-group" style={{ display: "flex", flexDirection: "column", alignItems: "center", width: "100%" }}>
                <div className="workflow-layer-title" style={{ width: "100%", display: "flex", alignItems: "center", gap: "10px", fontSize: "14px", fontWeight: "700", textTransform: "uppercase", letterSpacing: "0.08em", color: "#a5b4fc", borderBottom: "1px solid rgba(255, 255, 255, 0.08)", paddingBottom: "10px", marginBottom: "20px" }}>
                  <CornerDownRight size={15} />
                  <span>{layer}</span>
                </div>
                <div className="workflow-nodes-row" style={{ display: "flex", flexWrap: "wrap", justifyContent: "center", gap: "28px", width: "100%", maxWidth: "1200px" }}>
                  {nodesInLayer.map((node) => {
                    const isActive = node.id === selectedId;
                    const isConnectedTarget = selectedNode?.connections?.includes(node.label);
                    const isConnectedSource = node.connections?.includes(selectedNode?.label || "");
                    const highlightBorder = isActive ? "2px solid var(--card-accent, #6366f1)" : isConnectedTarget ? "2px solid #0284c7" : isConnectedSource ? "2px solid #8b5cf6" : "1px solid rgba(255, 255, 255, 0.1)";
                    const highlightShadow = isActive ? "0 20px 30px -8px rgba(0, 0, 0, 0.6), 0 0 15px var(--card-accent, #6366f1)" : isConnectedTarget ? "0 10px 25px -5px rgba(2, 132, 199, 0.5)" : isConnectedSource ? "0 10px 25px -5px rgba(139, 92, 246, 0.5)" : "none";

                    return (
                      <div
                        key={node.id}
                        className={`workflow-card ${isActive ? "active" : ""}`}
                        style={{ 
                          "--card-accent": node.accent,
                          border: highlightBorder,
                          boxShadow: highlightShadow,
                          background: isActive ? "rgba(30, 41, 59, 0.85)" : (isConnectedTarget || isConnectedSource) ? "rgba(30, 41, 59, 0.65)" : "rgba(30, 41, 59, 0.45)",
                          flex: "1 1 300px",
                          maxWidth: "380px"
                        } as React.CSSProperties}
                        onClick={() => setSelectedId(node.id)}
                      >
                        {isConnectedTarget && (
                          <div className="workflow-card-connection-badge target" style={{ background: "linear-gradient(135deg, #0284c7 0%, #0369a1 100%)", color: "#fff", fontSize: "11px", fontWeight: "700", padding: "4px 10px", borderRadius: "12px", display: "flex", alignItems: "center", gap: "6px", marginBottom: "12px", width: "fit-content", boxShadow: "0 2px 8px rgba(2, 132, 199, 0.5)" }}>
                            <ArrowRight size={13} />
                            <span>Được gọi bởi {selectedNode.label.split(" ")[0]}</span>
                          </div>
                        )}
                        {isConnectedSource && (
                          <div className="workflow-card-connection-badge source" style={{ background: "linear-gradient(135deg, #8b5cf6 0%, #6d28d9 100%)", color: "#fff", fontSize: "11px", fontWeight: "700", padding: "4px 10px", borderRadius: "12px", display: "flex", alignItems: "center", gap: "6px", marginBottom: "12px", width: "fit-content", boxShadow: "0 2px 8px rgba(139, 92, 246, 0.5)" }}>
                            <GitCommit size={13} />
                            <span>Gọi tới {selectedNode.label.split(" ")[0]}</span>
                          </div>
                        )}
                        <div className="workflow-card-header">
                          <div className="workflow-card-icon">
                            <CanvasNodeIcon name={node.iconName} accent={node.accent} />
                          </div>
                          <div className="workflow-card-meta">
                            <span className="workflow-card-c4">{node.c4Level}</span>
                            <span className={`workflow-card-status ${node.status}`}>
                              {node.status === "ready" && "● Ready"}
                              {node.status === "warning" && "▲ Needs Attention"}
                              {node.status === "error" && "■ Blocked"}
                            </span>
                          </div>
                        </div>
                        <div className="workflow-card-title">{node.label}</div>
                        <div className="workflow-card-desc">{node.desc}</div>
                        {node.connections && (
                          <div className="workflow-card-connections" style={{ marginTop: "10px", paddingTop: "8px", borderTop: "1px dashed var(--border-color, #334155)", display: "flex", alignItems: "center", gap: "6px", fontSize: "11px", color: "#94a3b8" }}>
                            <GitCommit size={13} color={node.accent} />
                            <span><strong>Liên kết:</strong> {node.connections.join(", ")}</span>
                          </div>
                        )}
                        <div className="workflow-card-tags">
                          {node.tags.map((tag) => (
                            <span key={tag} className="workflow-tag">{tag}</span>
                          ))}
                        </div>
                      </div>
                    );
                  })}
                </div>
                {layerIndex < layers.length - 1 && (
                  <div className="workflow-vertical-connector" style={{ display: "flex", flexDirection: "column", alignItems: "center", width: "100%", margin: "12px 0" }}>
                    {/* Trunk trên */}
                    <div style={{ width: "3px", height: "28px", background: "linear-gradient(180deg, #6366f1 0%, #06b6d4 100%)", boxShadow: "0 0 12px rgba(6, 182, 212, 0.6)", borderRadius: "2px" }} />
                    
                    {/* Huy hiệu HLD */}
                    <div className="workflow-connection-badge" style={{
                      position: "relative",
                      background: "rgba(15, 23, 42, 0.95)",
                      backdropFilter: "blur(12px)",
                      color: "#38bdf8",
                      border: "1px solid #0284c7",
                      padding: "8px 24px",
                      borderRadius: "30px",
                      display: "flex",
                      alignItems: "center",
                      gap: "8px",
                      fontSize: "13px",
                      fontWeight: "600",
                      boxShadow: "0 4px 20px rgba(2, 132, 199, 0.5)",
                      zIndex: 2
                    }}>
                      <GitMerge size={16} className="animate-pulse" style={{ color: "#38bdf8" }} />
                      <span>{getLayerConnectionLabel(layer, layers[layerIndex + 1])}</span>
                    </div>

                    {/* Trunk dưới */}
                    <div style={{ width: "3px", height: "28px", background: "linear-gradient(180deg, #06b6d4 0%, #10b981 100%)", boxShadow: "0 0 12px rgba(16, 185, 129, 0.6)", borderRadius: "2px" }} />

                    {/* Thanh ngang rẽ nhánh (branching bar) nếu layer dưới có nhiều node */}
                    {nextLayerNodes.length > 1 && (
                      <div style={{
                        width: `${Math.min(85, nextLayerNodes.length * 22)}%`,
                        maxWidth: `${(nextLayerNodes.length - 1) * 330}px`,
                        height: "3px",
                        background: "linear-gradient(90deg, transparent 0%, #10b981 15%, #10b981 85%, transparent 100%)",
                        boxShadow: "0 0 12px rgba(16, 185, 129, 0.6)",
                        marginTop: "-1px",
                        borderRadius: "2px"
                      }} />
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>

        {selectedNode && (
          <>
            {/* Thanh kéo thả để mở rộng (Resizer bar) */}
            <div
              className="workflow-inspector-resizer"
              onMouseDown={handleMouseDown}
              style={{
                width: "6px",
                cursor: "col-resize",
                background: "var(--border-color, #334155)",
                zIndex: 50,
                transition: "background 0.2s",
                position: "relative",
              }}
              onMouseEnter={(e) => { e.currentTarget.style.background = "#06b6d4"; }}
              onMouseLeave={(e) => { if (!isDraggingRef.current) e.currentTarget.style.background = "var(--border-color, #334155)"; }}
            >
              <div style={{ position: "absolute", top: "50%", left: "1px", width: "4px", height: "24px", background: "#cbd5e1", borderRadius: "2px", transform: "translateY(-50%)" }} />
            </div>

            <div className="workflow-inspector" style={{ width: `${inspectorWidth}px`, flexShrink: 0, overflowY: "auto", borderLeft: "1px solid var(--border-color, #334155)" }}>
              <div className="workflow-inspector-header">
                <div className="workflow-inspector-title">
                  <Info size={18} color={selectedNode.accent} />
                  <span>Chi tiết Component</span>
                </div>
                <button
                  type="button"
                  className="workflow-inspector-close"
                  onClick={() => setSelectedId("")}
                  title="Đóng"
                >
                  <X size={18} />
                </button>
              </div>
              <div className="workflow-inspector-body">
                <div className="inspector-section">
                  <div className="inspector-section-title">Thông tin chung</div>
                  <div className="inspector-card">
                    <div className="inspector-item">
                      <strong>Tên:</strong> <span>{selectedNode.label}</span>
                    </div>
                    <div className="inspector-item">
                      <strong>C4 Level:</strong> <span>{selectedNode.c4Level}</span>
                    </div>
                    <div className="inspector-item">
                      <strong>Layer:</strong> <span>{selectedNode.layer}</span>
                    </div>
                    <div className="inspector-item">
                      <strong>Vai trò:</strong> <span>{selectedNode.desc}</span>
                    </div>
                  </div>
                </div>

                {selectedNode.connections && (
                  <div className="inspector-section">
                    <div className="inspector-section-title">Liên kết kiến trúc HLD</div>
                    <div className="inspector-card">
                      <div className="inspector-item">
                        <strong>Thành phần kết nối:</strong> <span>{selectedNode.connections.join(", ")}</span>
                      </div>
                    </div>
                  </div>
                )}

                {selectedNode.codeFile && (
                  <div className="inspector-section">
                    <div className="inspector-section-title">
                      <FileCode size={14} />
                      <span>File Triển Khai</span>
                    </div>
                    <div className="inspector-code">{selectedNode.codeFile}</div>
                  </div>
                )}

                {selectedNode.contract && (
                  <div className="inspector-section">
                    <div className="inspector-section-title">Data Contract & Đặc tả</div>
                    <div className="inspector-card">
                      {selectedNode.contract.input && (
                        <div className="inspector-item">
                          <strong>Input:</strong> <span>{selectedNode.contract.input}</span>
                        </div>
                      )}
                      {selectedNode.contract.output && (
                        <div className="inspector-item">
                          <strong>Output:</strong> <span>{selectedNode.contract.output}</span>
                        </div>
                      )}
                      {selectedNode.contract.description && (
                        <div className="inspector-item">
                          <strong>Luồng thực thi:</strong> <span>{selectedNode.contract.description}</span>
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
