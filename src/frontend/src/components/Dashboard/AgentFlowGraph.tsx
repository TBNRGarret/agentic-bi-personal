import { Database, Sparkles, User, Zap, CheckCircle2, AlertCircle, Loader2 } from "lucide-react";

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
  if (workflowGraph?.nodes?.length) {
    return workflowGraph.nodes.map((node, index) => ({
      id: node.id || "workflow-" + index,
      label: node.label || formatStageName(node.kind || "workflow"),
      sublabel: graphSublabel(node),
      type: graphNodeType(node),
      active: Boolean(loading && index === (workflowGraph.nodes?.length || 0) - 1),
    }));
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

    const lower = name.toLowerCase();
    if (lower.includes("plan") || lower.includes("rout") || lower.includes("intent")) {
      nodes.push({
        id: `stage-${nodes.length}`,
        label: "Planner",
        sublabel: stage.intent || name,
        type: "planner",
      });
    } else if (lower.includes("sql") || lower.includes("query") || lower.includes("generat")) {
      nodes.push({
        id: `stage-${nodes.length}`,
        label: "SQL Agent",
        sublabel: stage.datasource || name,
        type: "agent",
      });
    } else if (lower.includes("forecast") || lower.includes("predict")) {
      nodes.push({
        id: `stage-${nodes.length}`,
        label: "Forecast Agent",
        sublabel: name,
        type: "agent",
      });
    } else if (lower.includes("partition")) {
      nodes.push({
        id: `stage-${nodes.length}`,
        label: "Partition Agent",
        sublabel: name,
        type: "agent",
      });
    } else if (lower.includes("monitor") || lower.includes("observe")) {
      nodes.push({
        id: `stage-${nodes.length}`,
        label: "Monitor Agent",
        sublabel: name,
        type: "agent",
      });
    } else if (lower.includes("execut") || lower.includes("run")) {
      nodes.push({
        id: `stage-${nodes.length}`,
        label: "Executor",
        sublabel: name,
        type: "agent",
      });
    } else {
      nodes.push({
        id: `stage-${nodes.length}`,
        label: formatStageName(name),
        sublabel: undefined,
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
      if (intent) {
        nodes.push({
          id: "planner-final",
          label: "Planner",
          sublabel: intent,
          type: "planner",
        });
      }
      if (agent) {
        nodes.push({
          id: "agent-final",
          label: formatStageName(agent),
          sublabel: undefined,
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
  const graphLabel = graphEdgeLabel(graph, prevNode.id, currNode.id);
  if (graphLabel) return graphLabel;
  if (prevNode.type === "user") return "analyzes request";
  if (prevNode.type === "planner") {
    if (currNode.type === "agent") return "routes to agent";
    if (currNode.type === "datasource") return "targets table";
    return "delegates to";
  }
  if (prevNode.type === "agent") {
    if (currNode.type === "datasource") return "queries database";
    if (currNode.type === "result" || currNode.type === "error") return "delivers response";
    return "passes data";
  }
  if (currNode.type === "datasource") return "accesses tables";
  if (currNode.type === "result") return "renders rows";
  if (currNode.type === "error") return "fails query";
  return "flows to";
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
