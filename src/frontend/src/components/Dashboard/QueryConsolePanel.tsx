import { useEffect, useMemo, useRef, useState } from "react";
import {
  BarChart3,
  Calculator,
  Clipboard,
  Copy,
  Cpu,
  Database,
  Download,
  FileSearch,
  History,
  LineChart,
  PieChart,
  Play,
  RefreshCcw,
  RotateCcw,
  Send,
  Star,
  Table,
  Terminal,
  Trash2,
  Wallet,
} from "lucide-react";
import { DynamicChart, type ChartSpec, type ChartType } from "../Charts/DynamicChart";
import { api } from "../../services/api.service";
import { AgentFlowGraph } from "./AgentFlowGraph";

interface QueryResult {
  success?: boolean;
  rowCount?: number;
  rows?: Record<string, unknown>[];
  executionTimeMs?: number;
  error?: string;
  queryId?: string;
}

interface ExplainResult {
  success?: boolean;
  sql?: string;
  tables?: string[];
  explanation?: string;
  explanationSource?: "llm" | "rule_based" | string;
  warnings?: string[];
  profile?: Record<string, unknown>;
  llmUsage?: Record<string, unknown> | null;
  estimatedCostUsd?: number | null;
  executionTimeMs?: number;
  queryId?: string;
}

interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  data?: Record<string, unknown>;
  stages?: StreamEvent[];
  chartOverride?: ChartType;
}

interface StreamEvent {
  type?: "stage" | "sql" | "delta" | "final";
  stage?: string;
  message?: string;
  text?: string;
  sql?: string;
  intent?: string;
  confidence?: number;
  datasource?: string;
  payload?: Record<string, unknown>;
}

interface ClarificationOption {
  label?: string;
  value?: string;
  questionSuffix?: string;
  description?: string;
}

interface ClarificationPayload {
  tool?: string;
  question?: string;
  reason?: string;
  options?: ClarificationOption[];
}

interface QueryHistoryItem {
  id: string;
  kind: Mode;
  title: string;
  sql?: string;
  question?: string;
  target?: Target;
  summary?: string;
  rowCount?: number;
  executionTimeMs?: number;
  costUsd?: number;
  favorite?: boolean;
  createdAt: string;
}

interface BiCostEstimate {
  model: string;
  inputTokens: number;
  outputTokens: number;
  totalTokens: number;
  costUsd: number;
}

type Mode = "sql" | "chat";
type Target = "auto" | "dwh" | "clickhouse";
type AgentModel = "model1" | "model2";
type HistoryFilter = "recent" | "favorites";

const HISTORY_KEY = "observation.bi.queryHistory.v1";
const SESSION_COST_KEY = "observation.bi.sessionCostUsd.v1";
const SESSION_TOKEN_KEY = "observation.bi.sessionTokens.v1";
const BI_MODEL =
  import.meta.env.VITE_BI_LLM_MODEL ||
  import.meta.env.VITE_LLM_MODEL ||
  "openai/gpt-oss-120b:free";

const CHART_OPTIONS: Array<{ type: ChartType; label: string; icon: typeof Table }> = [
  { type: "table", label: "Table", icon: Table },
  { type: "bar", label: "Bar", icon: BarChart3 },
  { type: "line", label: "Line", icon: LineChart },
  { type: "pie", label: "Pie", icon: PieChart },
  { type: "scatter", label: "Scatter", icon: LineChart },
  { type: "histogram", label: "Histogram", icon: BarChart3 },
  { type: "box", label: "Box", icon: BarChart3 },
  { type: "heatmap", label: "Heatmap", icon: BarChart3 },
];

function getRows(data?: Record<string, unknown>): Record<string, unknown>[] {
  return Array.isArray(data?.rows) ? (data.rows as Record<string, unknown>[]) : [];
}

function getChartSpec(data?: Record<string, unknown>): ChartSpec {
  const base = typeof data?.chartSpec === "object" && data.chartSpec ? (data.chartSpec as ChartSpec) : { type: "table" as ChartType };
  const dsl = typeof data?.visualizationDsl === "object" && data.visualizationDsl
    ? (data.visualizationDsl as Record<string, unknown>)
    : null;
  const config = typeof dsl?.config === "object" && dsl.config ? (dsl.config as Record<string, unknown>) : null;
  const layout = typeof dsl?.layout === "object" && dsl.layout ? (dsl.layout as Record<string, unknown>) : null;
  const dslType = typeof dsl?.chart_type === "string" && isChartType(dsl.chart_type) ? dsl.chart_type : base.type;
  const x = stringValue(config?.x) || stringValue(config?.labels) || base.x;
  const y = stringListValue(config?.y) || stringValue(config?.values) || base.y;
  return {
    ...base,
    type: dslType,
    x,
    y,
    title: stringValue(layout?.title) || base.title,
  };
}

function getClarification(data?: Record<string, unknown>): ClarificationPayload | null {
  const clarification = typeof data?.clarification === "object" && data.clarification
    ? (data.clarification as ClarificationPayload)
    : null;
  if (!clarification || !Array.isArray(clarification.options) || clarification.options.length === 0) {
    return null;
  }
  return clarification;
}

function buildBiConversation(messages: ChatMessage[]): Record<string, unknown>[] {
  return messages
    .slice(-12)
    .filter((message) => message.content.trim() || message.data)
    .map((message) => {
      const data = message.data || {};
      const compactData: Record<string, unknown> = {};
      ["sql", "target", "status", "rowCount", "intent"].forEach((key) => {
        if (data[key] !== undefined && data[key] !== null && data[key] !== "") {
          compactData[key] = data[key];
        }
      });
      if (typeof data.chartSpec === "object" && data.chartSpec) {
        compactData.chartSpec = data.chartSpec;
      }
      return {
        role: message.role,
        content: message.content,
        data: compactData,
      };
    });
}

function createDeltaBatcher(append: (text: string) => void) {
  let buffer = "";
  let timer: ReturnType<typeof setTimeout> | undefined;
  const flush = () => {
    if (timer) clearTimeout(timer);
    timer = undefined;
    if (!buffer) return;
    const text = buffer;
    buffer = "";
    append(text);
  };
  return {
    push(text: string) {
      buffer += text;
      if (!timer) timer = setTimeout(flush, 50);
    },
    flush,
  };
}

function isChartType(value: string): value is ChartType {
  return (
    value === "table" ||
    value === "bar" ||
    value === "line" ||
    value === "pie" ||
    value === "scatter" ||
    value === "histogram" ||
    value === "box" ||
    value === "heatmap"
  );
}

function stringValue(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value : undefined;
}

function stringListValue(value: unknown): string[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const values = value.filter((item): item is string => typeof item === "string" && Boolean(item.trim()));
  return values.length ? values : undefined;
}

function cellValue(value: unknown): string {
  if (value === null || value === undefined) return "NULL";
  if (typeof value === "object") return JSON.stringify(value).slice(0, 160);
  return String(value).slice(0, 200);
}

function numberValue(value: unknown): number | undefined {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string") {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return undefined;
}

function makeId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function readStoredNumber(key: string): number {
  if (typeof window === "undefined") return 0;
  const value = Number(window.localStorage.getItem(key));
  return Number.isFinite(value) ? value : 0;
}

function loadHistory(): QueryHistoryItem[] {
  if (typeof window === "undefined") return [];
  try {
    const parsed = JSON.parse(window.localStorage.getItem(HISTORY_KEY) || "[]");
    return Array.isArray(parsed) ? parsed.filter((item) => item?.id && item?.kind && item?.createdAt) : [];
  } catch {
    return [];
  }
}

function persistHistory(items: QueryHistoryItem[]): QueryHistoryItem[] {
  if (typeof window !== "undefined") {
    window.localStorage.setItem(HISTORY_KEY, JSON.stringify(items));
  }
  return items;
}

function historySignature(item: Pick<QueryHistoryItem, "kind" | "sql" | "question" | "target">): string {
  return [item.kind, item.sql?.trim() || "", item.question?.trim() || "", item.target || ""].join("::");
}

function trimHistory(items: QueryHistoryItem[]): QueryHistoryItem[] {
  const sorted = [...items].sort((a, b) => Date.parse(b.createdAt) - Date.parse(a.createdAt));
  const favorites = sorted.filter((item) => item.favorite).slice(0, 50);
  const recents = sorted.filter((item) => !item.favorite).slice(0, 20);
  const byId = new Map<string, QueryHistoryItem>();
  [...favorites, ...recents].forEach((item) => byId.set(item.id, item));
  return Array.from(byId.values()).sort((a, b) => Date.parse(b.createdAt) - Date.parse(a.createdAt));
}

function shortTitle(text: string, fallback: string): string {
  const normalized = text.replace(/\s+/g, " ").trim();
  if (!normalized) return fallback;
  return normalized.length > 72 ? `${normalized.slice(0, 69)}...` : normalized;
}

function estimateTokens(text: string): number {
  const trimmed = text.trim();
  return trimmed ? Math.max(1, Math.ceil(trimmed.length / 4)) : 0;
}

function estimateBiChatCost(inputText: string, outputText = "", model = BI_MODEL): BiCostEstimate {
  const inputTokens = estimateTokens(inputText);
  const outputTokens = outputText ? estimateTokens(outputText) : inputTokens > 0 ? Math.max(350, Math.ceil(inputTokens * 0.6)) : 0;
  const modelName = model.toLowerCase();
  const costUsd =
    modelName.includes(":free") || modelName.endsWith("-free")
      ? 0
      : modelName.includes("gpt-4")
        ? (inputTokens * 5 + outputTokens * 15) / 1_000_000
        : (inputTokens * 0.15 + outputTokens * 0.6) / 1_000_000;
  return {
    model,
    inputTokens,
    outputTokens,
    totalTokens: inputTokens + outputTokens,
    costUsd,
  };
}

function extractCostFromPayload(payload?: Record<string, unknown>): number | undefined {
  const usage = typeof payload?.llmUsage === "object" && payload.llmUsage ? payload.llmUsage as Record<string, unknown> : null;
  const synthesis = typeof payload?.llmSynthesis === "object" && payload.llmSynthesis ? payload.llmSynthesis as Record<string, unknown> : null;
  const candidates = [
    payload?.estimatedCostUsd,
    payload?.totalCostUsd,
    usage?.estimatedCostUsd,
    usage?.costUsd,
    synthesis?.estimatedCostUsd,
    synthesis?.totalCostUsd,
    synthesis?.costUsd,
  ];
  for (const candidate of candidates) {
    const value = numberValue(candidate);
    if (value !== undefined) return value;
  }
  return undefined;
}

function extractTokensFromPayload(payload?: Record<string, unknown>): number | undefined {
  const usage = typeof payload?.llmUsage === "object" && payload.llmUsage ? payload.llmUsage as Record<string, unknown> : null;
  const synthesis = typeof payload?.llmSynthesis === "object" && payload.llmSynthesis ? payload.llmSynthesis as Record<string, unknown> : null;
  const candidates = [
    payload?.totalTokens,
    usage?.totalTokens,
    synthesis?.totalTokens,
    synthesis?.tokens,
  ];
  for (const candidate of candidates) {
    const value = numberValue(candidate);
    if (value !== undefined) return Math.round(value);
  }
  return undefined;
}

function formatUsd(value?: number | null): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "$0.000000";
  return `$${value.toFixed(value >= 0.01 ? 4 : 6)}`;
}

function formatTime(value: string): string {
  try {
    return new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date(value));
  } catch {
    return value;
  }
}

function csvEscape(value: unknown): string {
  const text = value === null || value === undefined ? "" : String(value);
  return `"${text.replace(/"/g, '""')}"`;
}

function exportHistoryCsv(items: QueryHistoryItem[]) {
  if (!items.length || typeof document === "undefined") return;
  const headers = ["kind", "favorite", "title", "target", "rowCount", "executionTimeMs", "costUsd", "createdAt", "sql", "question"];
  const rows = items.map((item) => [
    item.kind,
    item.favorite ? "yes" : "no",
    item.title,
    item.target || "",
    item.rowCount ?? "",
    item.executionTimeMs ?? "",
    item.costUsd ?? "",
    item.createdAt,
    item.sql || "",
    item.question || "",
  ]);
  const csv = [headers, ...rows].map((row) => row.map(csvEscape).join(",")).join("\n");
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `query-history-${new Date().toISOString().slice(0, 10)}.csv`;
  anchor.click();
  URL.revokeObjectURL(url);
}

function updateAssistant(
  messages: ChatMessage[],
  index: number,
  updater: (message: ChatMessage) => ChatMessage,
) {
  return messages.map((message, messageIndex) => (messageIndex === index ? updater(message) : message));
}

async function readBiStream(response: Response, onEvent: (event: StreamEvent) => void) {
  const reader = response.body?.getReader();
  if (!reader) return;
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const events = buffer.split("\n\n");
    buffer = events.pop() || "";

    for (const rawEvent of events) {
      const dataLine = rawEvent.split("\n").find((line) => line.startsWith("data: "));
      if (!dataLine) continue;
      try {
        onEvent(JSON.parse(dataLine.slice(6)) as StreamEvent);
      } catch {
        // Ignore malformed stream fragments.
      }
    }
  }
}

function StageTrail({ stages }: { stages?: StreamEvent[] }) {
  if (!stages?.length) return null;
  return (
    <div className="bi-stage-trail">
      {stages.map((stage, index) => (
        <span key={`${stage.stage || "stage"}-${index}`} className="bi-stage-chip">
          <span className="status-dot up" />
          {stage.stage || "stage"}
        </span>
      ))}
    </div>
  );
}

function ResultTable({ rows }: { rows: Record<string, unknown>[] }) {
  const [filterText, setFilterText] = useState("");
  const [sortCol, setSortCol] = useState<string | null>(null);
  const [sortAsc, setSortAsc] = useState(true);
  const [page, setPage] = useState(1);
  const pageSize = 10;

  if (!rows.length) return null;
  const columns = Object.keys(rows[0]);

  const filteredRows = useMemo(() => {
    if (!filterText.trim()) return rows;
    const query = filterText.toLowerCase();
    return rows.filter((row) =>
      columns.some((col) => cellValue(row[col]).toLowerCase().includes(query))
    );
  }, [rows, columns, filterText]);

  const sortedRows = useMemo(() => {
    if (!sortCol) return filteredRows;
    return [...filteredRows].sort((a, b) => {
      const valA = a[sortCol];
      const valB = b[sortCol];
      const numA = numberValue(valA);
      const numB = numberValue(valB);
      if (numA !== undefined && numB !== undefined) {
        return sortAsc ? numA - numB : numB - numA;
      }
      const strA = cellValue(valA).toLowerCase();
      const strB = cellValue(valB).toLowerCase();
      return sortAsc ? strA.localeCompare(strB) : strB.localeCompare(strA);
    });
  }, [filteredRows, sortCol, sortAsc]);

  const totalPages = Math.max(1, Math.ceil(sortedRows.length / pageSize));
  const currentPage = Math.min(page, totalPages);
  const paginatedRows = sortedRows.slice((currentPage - 1) * pageSize, currentPage * pageSize);

  const handleSort = (col: string) => {
    if (sortCol === col) {
      setSortAsc(!sortAsc);
    } else {
      setSortCol(col);
      setSortAsc(true);
    }
  };

  return (
    <div className="chat-result-table premium-glass-panel" style={{ margin: "16px 0", overflow: "hidden" }}>
      <div className="table-filter-bar">
        <FileSearch size={16} color="var(--text-muted)" />
        <input
          type="text"
          className="table-filter-input"
          placeholder="Instant filter in results..."
          value={filterText}
          onChange={(e) => {
            setFilterText(e.target.value);
            setPage(1);
          }}
        />
        <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
          {sortedRows.length} {sortedRows.length === 1 ? "row" : "rows"} found
        </span>
      </div>
      <div style={{ overflowX: "auto" }}>
        <table className="data-table">
          <thead>
            <tr>
              {columns.map((column) => (
                <th
                  key={column}
                  className="clickable-th"
                  onClick={() => handleSort(column)}
                  title="Click to sort"
                >
                  {column} {sortCol === column ? (sortAsc ? "▲" : "▼") : ""}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {paginatedRows.map((row, rowIndex) => (
              <tr key={rowIndex}>
                {columns.map((column) => (
                  <td key={column}>{cellValue(row[column])}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {sortedRows.length > pageSize && (
        <div className="pagination-toolbar">
          <span>
            Showing {(currentPage - 1) * pageSize + 1} - {Math.min(currentPage * pageSize, sortedRows.length)} of {sortedRows.length}
          </span>
          <div className="pagination-controls">
            <button
              type="button"
              className="pagination-btn"
              disabled={currentPage === 1}
              onClick={() => setPage(currentPage - 1)}
            >
              Previous
            </button>
            <span style={{ fontSize: 12, color: "var(--text-primary)" }}>
              Page {currentPage} of {totalPages}
            </span>
            <button
              type="button"
              className="pagination-btn"
              disabled={currentPage === totalPages}
              onClick={() => setPage(currentPage + 1)}
            >
              Next
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function ExplainPanel({ result, error }: { result: ExplainResult | null; error: string }) {
  if (!result && !error) return null;
  const sourceLabel = result?.explanationSource === "llm" ? "LLM Vietnamese" : "Rule fallback";
  return (
    <div className={`sql-explain-panel ${error ? "sql-explain-panel--error" : ""}`}>
      <div className="sql-explain-header">
        <div>
          <div className="sql-explain-title">SQL explanation</div>
          {result?.executionTimeMs !== undefined && (
            <div className="sql-explain-meta">
              {result.executionTimeMs}ms - {sourceLabel}
              {result.estimatedCostUsd !== undefined && result.estimatedCostUsd !== null ? ` - ${formatUsd(result.estimatedCostUsd)}` : ""}
              {result.queryId ? ` - ${result.queryId}` : ""}
            </div>
          )}
        </div>
      </div>
      {error ? (
        <div className="sql-explain-error">{error}</div>
      ) : (
        <>
          <p className="sql-explain-text">{result?.explanation || "No explanation returned."}</p>
          {result?.tables && result.tables.length > 0 && (
            <div className="sql-explain-chips">
              {result.tables.map((table) => (
                <span key={table}>{table}</span>
              ))}
            </div>
          )}
          {result?.warnings && result.warnings.length > 0 && (
            <div className="sql-explain-warnings">
              {result.warnings.map((warning) => (
                <div key={warning}>{warning}</div>
              ))}
            </div>
          )}
          {result?.profile && (
            <div className="sql-explain-profile">
              {Object.entries(result.profile).map(([key, value]) => (
                <span key={key}>
                  {key}: {cellValue(value)}
                </span>
              ))}
            </div>
          )}
          {result?.sql && <pre className="sql-explain-sql">{result.sql}</pre>}
        </>
      )}
    </div>
  );
}

function ClarificationPanel({
  data,
  onSelect,
}: {
  data?: Record<string, unknown>;
  onSelect: (questionSuffix: string) => void;
}) {
  const clarification = getClarification(data);
  if (!clarification) return null;

  return (
    <div className="premium-glass-panel" style={{ padding: 24, margin: "16px 0" }}>
      <div className="premium-modal-title" style={{ marginBottom: 16 }}>
        {clarification.question || "Clarify request"}
      </div>
      <div className="premium-card-grid">
        {clarification.options?.map((option) => {
          const suffix = option.questionSuffix || option.value || option.label || "";
          return (
            <div
              key={option.value || option.label || suffix}
              className="interactive-card"
              onClick={() => suffix && onSelect(suffix)}
              style={{ display: "flex", flexDirection: "column", gap: 8 }}
            >
              <strong style={{ color: "var(--neon-orange)", fontSize: 15 }}>
                {option.label || option.value || "Option"}
              </strong>
              {option.description && (
                <span style={{ color: "var(--text-secondary)", fontSize: 13 }}>
                  {option.description}
                </span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function ChatResultPreview({
  data,
  chartOverride,
  onChartTypeChange,
  onRetry,
  onClarificationSelect,
  onFollowup,
}: {
  data?: Record<string, unknown>;
  chartOverride?: ChartType;
  onChartTypeChange: (type: ChartType) => void;
  onRetry: () => void;
  onClarificationSelect: (questionSuffix: string) => void;
  onFollowup: (question: string) => void;
}) {
  const [showCompareModal, setShowCompareModal] = useState(false);
  const [manualSql, setManualSql] = useState("");

  const rows = getRows(data);
  const chart = getChartSpec(data);
  const sql = typeof data?.sql === "string" ? data.sql : "";
  const status = typeof data?.status === "string" ? data.status : "";
  const profile = typeof data?.profile === "object" && data.profile ? (data.profile as Record<string, unknown>) : null;
  const visualizationDsl = typeof data?.visualizationDsl === "object" && data.visualizationDsl
    ? (data.visualizationDsl as Record<string, unknown>)
    : undefined;
  const selectedType = chartOverride || chart.type || "table";
  const cost = extractCostFromPayload(data);
  const tokens = extractTokensFromPayload(data);
  const suggestions = Array.isArray(data?.suggestions)
    ? data.suggestions.filter((item): item is string => typeof item === "string")
    : [];

  const autoOptimizeChart = () => {
    if (!rows.length) return;
    const sample = rows[0];
    const keys = Object.keys(sample);
    const hasDate = keys.some((k) => k.toLowerCase().includes("date") || k.toLowerCase().includes("time"));
    if (hasDate) {
      onChartTypeChange("line");
      return;
    }
    if (rows.length <= 5 && rows.length >= 2) {
      onChartTypeChange("pie");
      return;
    }
    onChartTypeChange("bar");
  };

  if (!data && !sql) return null;

  return (
    <div className="chat-result-preview">
      <div className="bi-result-toolbar" style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        {rows.length >= 2 && (
          <div className="bi-chart-toggle" role="group" aria-label="Chart type">
            {CHART_OPTIONS.map((option) => {
              const Icon = option.icon;
              return (
                <button
                  key={option.type}
                  type="button"
                  title={option.label}
                  className={selectedType === option.type ? "active" : ""}
                  onClick={() => onChartTypeChange(option.type)}
                >
                  <Icon size={14} />
                </button>
              );
            })}
          </div>
        )}
        {rows.length >= 2 && (
          <button className="btn btn-secondary bi-mini-btn" type="button" onClick={autoOptimizeChart} title="Auto select best chart for data">
            <Star size={14} color="var(--neon-orange)" />
            Auto-Optimize Chart
          </button>
        )}
        {sql && (
          <button className="btn btn-secondary bi-mini-btn" type="button" onClick={() => setShowCompareModal(true)}>
            <Cpu size={14} color="var(--accent-cyan)" />
            Compare AI vs Manual SQL
          </button>
        )}
        {status === "error" && (
          <button className="btn btn-secondary bi-mini-btn" type="button" onClick={onRetry}>
            <RefreshCcw size={14} />
            Retry
          </button>
        )}
      </div>

      {status === "needs_clarification" && (
        <ClarificationPanel data={data} onSelect={onClarificationSelect} />
      )}

      {typeof data?.routeNote === "string" && data.routeNote && (
        <div className="bi-route-note">{data.routeNote}</div>
      )}

      {rows.length >= 2 && selectedType !== "table" && (
        <div className="premium-glass-panel" style={{ padding: 20, margin: "16px 0" }}>
          <DynamicChart chartSpec={chart} rows={rows} typeOverride={selectedType} visualizationDsl={visualizationDsl} />
        </div>
      )}

      <ResultTable rows={rows} />

      {(cost !== undefined || tokens !== undefined) && (
        <div className="bi-response-cost">
          {tokens ?? 0} tokens / {formatUsd(cost ?? 0)}
        </div>
      )}

      {suggestions.length > 0 && (
        <div className="bi-followup-list">
          {suggestions.map((suggestion) => (
            <button
              key={suggestion}
              type="button"
              onClick={() => onFollowup(suggestion)}
            >
              {suggestion}
            </button>
          ))}
        </div>
      )}

      {sql && (
        <details className="chat-sql-details premium-glass-panel" style={{ padding: 16, marginTop: 16 }}>
          <summary style={{ cursor: "pointer", fontWeight: 700, color: "var(--text-primary)" }}>SQL and profile</summary>
          <div className="bi-sql-actions" style={{ marginTop: 12 }}>
            <button className="btn btn-secondary bi-mini-btn" type="button" onClick={() => navigator.clipboard?.writeText(sql)}>
              <Copy size={14} />
              Copy SQL
            </button>
          </div>
          <pre style={{ marginTop: 12, background: "var(--bg-primary)", padding: 12, borderRadius: 8 }}>{sql}</pre>
          {profile && (
            <div className="chat-profile" style={{ marginTop: 12 }}>
              {Object.entries(profile).map(([key, value]) => (
                <span key={key}>
                  {key}: {cellValue(value)}
                </span>
              ))}
            </div>
          )}
        </details>
      )}

      {showCompareModal && (
        <div className="modal-backdrop" onClick={() => setShowCompareModal(false)}>
          <div className="premium-modal" onClick={(e) => e.stopPropagation()}>
            <div className="premium-modal-header">
              <span className="premium-modal-title">AI vs Manual SQL Comparison</span>
              <button type="button" className="premium-modal-close" onClick={() => setShowCompareModal(false)}>×</button>
            </div>
            <div style={{ display: "grid", gap: 16, gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))" }}>
              <div className="premium-glass-panel" style={{ padding: 16 }}>
                <h4 style={{ color: "var(--neon-orange)", marginBottom: 8 }}>AI Generated SQL</h4>
                <pre style={{ background: "var(--bg-primary)", padding: 12, borderRadius: 8, fontSize: 12, maxHeight: 200, overflow: "auto" }}>{sql}</pre>
                <div style={{ marginTop: 12, fontSize: 12, color: "var(--text-secondary)" }}>
                  <div>Estimated Tokens: {estimateTokens(sql)}</div>
                  <div>Optimized for ClickHouse Gold tables</div>
                </div>
              </div>
              <div className="premium-glass-panel" style={{ padding: 16 }}>
                <h4 style={{ color: "var(--accent-cyan)", marginBottom: 8 }}>Your Manual SQL</h4>
                <textarea
                  style={{ width: "100%", height: 140, background: "var(--bg-input)", color: "var(--text-primary)", border: "1px solid var(--border-color)", borderRadius: 8, padding: 12, fontSize: 12, fontFamily: "var(--font-mono)" }}
                  placeholder="Paste or type your SQL here to compare..."
                  value={manualSql}
                  onChange={(e) => setManualSql(e.target.value)}
                />
                <div style={{ marginTop: 12, fontSize: 12, color: "var(--text-secondary)" }}>
                  <div>Estimated Tokens: {estimateTokens(manualSql)}</div>
                  <div>{manualSql.trim() ? (manualSql.length < sql.length ? "More compact than AI SQL" : "More verbose than AI SQL") : "Enter SQL to see insights"}</div>
                </div>
              </div>
            </div>
            <div style={{ display: "flex", justifyContent: "flex-end" }}>
              <button type="button" className="btn btn-primary" onClick={() => setShowCompareModal(false)}>Close Comparison</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function QueryHistoryPanel({
  items,
  allItems,
  filter,
  onFilterChange,
  onReplay,
  onToggleFavorite,
  onExport,
  onClearRecents,
}: {
  items: QueryHistoryItem[];
  allItems: QueryHistoryItem[];
  filter: HistoryFilter;
  onFilterChange: (filter: HistoryFilter) => void;
  onReplay: (item: QueryHistoryItem) => void;
  onToggleFavorite: (id: string) => void;
  onExport: () => void;
  onClearRecents: () => void;
}) {
  return (
    <div className="query-history-panel">
      <div className="query-history-header">
        <div className="query-history-title">
          <History size={15} />
          Query history
        </div>
        <div className="query-history-actions">
          <button
            type="button"
            title="Recent"
            className={`query-history-filter ${filter === "recent" ? "active" : ""}`}
            onClick={() => onFilterChange("recent")}
          >
            <History size={14} />
            Recent
          </button>
          <button
            type="button"
            title="Favorites"
            className={`query-history-filter ${filter === "favorites" ? "active" : ""}`}
            onClick={() => onFilterChange("favorites")}
          >
            <Star size={14} />
            Favorites
          </button>
          <button type="button" title="Export history CSV" className="query-icon-btn" disabled={!allItems.length} onClick={onExport}>
            <Download size={14} />
          </button>
          <button type="button" title="Clear non-favorite history" className="query-icon-btn" disabled={!allItems.some((item) => !item.favorite)} onClick={onClearRecents}>
            <Trash2 size={14} />
          </button>
        </div>
      </div>
      {items.length ? (
        <div className="query-history-list">
          {items.slice(0, 8).map((item) => (
            <div key={item.id} className="query-history-item">
              <button type="button" className="query-history-main" onClick={() => onReplay(item)} title="Replay query">
                <span className="query-history-kind">{item.kind === "sql" ? "SQL" : "BI"}</span>
                <span className="query-history-copy">
                  <span className="query-history-item-title">{item.title}</span>
                  <span className="query-history-meta">
                    {formatTime(item.createdAt)}
                    {item.target ? ` - ${item.target}` : ""}
                    {item.rowCount !== undefined ? ` - ${item.rowCount} rows` : ""}
                    {item.costUsd !== undefined ? ` - ${formatUsd(item.costUsd)}` : ""}
                  </span>
                </span>
              </button>
              <div className="query-history-item-actions">
                <button type="button" title="Replay" className="query-icon-btn" onClick={() => onReplay(item)}>
                  <RotateCcw size={14} />
                </button>
                <button
                  type="button"
                  title={item.favorite ? "Remove favorite" : "Favorite"}
                  className={`query-icon-btn ${item.favorite ? "active" : ""}`}
                  onClick={() => onToggleFavorite(item.id)}
                >
                  <Star size={14} fill={item.favorite ? "currentColor" : "none"} />
                </button>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="query-history-empty">
          {filter === "favorites" ? "No favorite queries yet." : "No query history yet."}
        </div>
      )}
    </div>
  );
}

export function QueryConsolePanel() {
  const [mode, setMode] = useState<Mode>("sql");
  const [sql, setSql] = useState("SELECT * FROM gold.gold_llm_usage_daily ORDER BY metric_date DESC LIMIT 20;");
  const [result, setResult] = useState<QueryResult | null>(null);
  const [sqlLoading, setSqlLoading] = useState(false);
  const [sqlError, setSqlError] = useState("");
  const [explainResult, setExplainResult] = useState<ExplainResult | null>(null);
  const [explainLoading, setExplainLoading] = useState(false);
  const [explainError, setExplainError] = useState("");
  const [chatTarget, setChatTarget] = useState<Target>("auto");
  const [chatModel, setChatModel] = useState<AgentModel>("model1");
  const [chatInput, setChatInput] = useState("");
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [chatLoading, setChatLoading] = useState(false);
  const [tables, setTables] = useState<Record<string, unknown>[] | null>(null);
  const [tablesLoading, setTablesLoading] = useState(false);
  const [history, setHistory] = useState<QueryHistoryItem[]>(() => loadHistory());
  const [historyFilter, setHistoryFilter] = useState<HistoryFilter>("recent");
  const [sessionCost, setSessionCost] = useState(() => readStoredNumber(SESSION_COST_KEY));
  const [sessionTokens, setSessionTokens] = useState(() => Math.round(readStoredNumber(SESSION_TOKEN_KEY)));
  const [chatSessionId, setChatSessionId] = useState("");
  const scrollRef = useRef<HTMLDivElement | null>(null);

  const visibleHistory = useMemo(
    () => (historyFilter === "favorites" ? history.filter((item) => item.favorite) : history),
    [history, historyFilter],
  );
  const draftCost = useMemo(() => estimateBiChatCost(chatInput), [chatInput]);

  useEffect(() => {
    scrollRef.current?.scrollIntoView({
      behavior: chatLoading ? "auto" : "smooth",
      block: "end",
    });
  }, [chatMessages.length, chatLoading]);

  const upsertHistoryItem = (draft: Omit<QueryHistoryItem, "id" | "createdAt"> & Partial<Pick<QueryHistoryItem, "id" | "createdAt">>) => {
    setHistory((prev) => {
      const signature = historySignature(draft);
      const existing = prev.find((item) => historySignature(item) === signature);
      const nextItem: QueryHistoryItem = {
        ...draft,
        id: existing?.id || draft.id || makeId(),
        favorite: existing?.favorite || draft.favorite || false,
        createdAt: new Date().toISOString(),
      };
      const next = trimHistory([nextItem, ...prev.filter((item) => item.id !== nextItem.id)]);
      return persistHistory(next);
    });
  };

  const toggleFavorite = (id: string) => {
    setHistory((prev) => persistHistory(prev.map((item) => (item.id === id ? { ...item, favorite: !item.favorite } : item))));
  };

  const clearRecentHistory = () => {
    setHistory((prev) => persistHistory(prev.filter((item) => item.favorite)));
  };

  const addSessionCost = (costUsd: number, tokens: number) => {
    setSessionCost((prev) => {
      const next = prev + costUsd;
      window.localStorage.setItem(SESSION_COST_KEY, String(next));
      return next;
    });
    setSessionTokens((prev) => {
      const next = prev + tokens;
      window.localStorage.setItem(SESSION_TOKEN_KEY, String(next));
      return next;
    });
  };

  const resetSessionCost = () => {
    setSessionCost(0);
    setSessionTokens(0);
    window.localStorage.setItem(SESSION_COST_KEY, "0");
    window.localStorage.setItem(SESSION_TOKEN_KEY, "0");
  };

  const replayHistoryItem = (item: QueryHistoryItem) => {
    if (item.kind === "sql" && item.sql) {
      setMode("sql");
      setSql(item.sql);
      setExplainResult(null);
      setResult(null);
      runQuery(item.sql);
      return;
    }
    if (item.kind === "chat" && item.question) {
      const target = item.target || "auto";
      setMode("chat");
      setChatTarget(target);
      sendChat(item.question, target);
    }
  };

  const runQuery = async (overrideSql?: string) => {
    const queryText = (overrideSql || sql).trim();
    if (!queryText) return;
    setSqlLoading(true);
    setSqlError("");
    setResult(null);
    try {
      const data = (await api.queryDwh(queryText)) as QueryResult;
      setResult(data);
      upsertHistoryItem({
        kind: "sql",
        title: shortTitle(queryText, "SQL query"),
        sql: queryText,
        summary: `${data.rowCount ?? 0} rows in ${data.executionTimeMs ?? 0}ms`,
        rowCount: data.rowCount,
        executionTimeMs: data.executionTimeMs,
      });
    } catch (e) {
      setSqlError((e as Error).message);
    } finally {
      setSqlLoading(false);
    }
  };

  const explainQuery = async () => {
    if (!sql.trim()) return;
    setExplainLoading(true);
    setExplainError("");
    setExplainResult(null);
    try {
      const data = (await api.explainSql(sql.trim())) as ExplainResult;
      setExplainResult(data);
    } catch (e) {
      setExplainError((e as Error).message);
    } finally {
      setExplainLoading(false);
    }
  };

  const recordBiFinal = (question: string, target: Target, payload: Record<string, unknown>, answer: string) => {
    if (payload.status === "needs_clarification") {
      return;
    }
    const generatedSql = typeof payload.sql === "string" ? payload.sql : "";
    const rows = getRows(payload);
    const rowCount = numberValue(payload.rowCount) ?? rows.length;
    const executionTimeMs = numberValue(payload.executionTimeMs);
    const estimated = estimateBiChatCost(`${question}\n${generatedSql}`, answer);
    const costUsd = extractCostFromPayload(payload) ?? estimated.costUsd;
    const tokens = extractTokensFromPayload(payload) ?? estimated.totalTokens;
    addSessionCost(costUsd, tokens);
    upsertHistoryItem({
      kind: "chat",
      title: shortTitle(question, "BI chat"),
      question,
      sql: generatedSql || undefined,
      target,
      summary: `${rowCount} rows - ${formatUsd(costUsd)}`,
      rowCount,
      executionTimeMs,
      costUsd,
    });
  };

  const sendChat = async (override?: string, overrideTarget?: Target) => {
    const question = (override || chatInput).trim();
    const target = overrideTarget || chatTarget;
    if (!question || chatLoading) return;

    const conversation = buildBiConversation(chatMessages);
    setChatInput("");
    const assistantIndex = chatMessages.length + 1;
    setChatMessages((prev) => [
      ...prev,
      { role: "user", content: question },
      { role: "assistant", content: "", stages: [] },
    ]);
    setChatLoading(true);
    const deltaBatcher = createDeltaBatcher((text) => {
      setChatMessages((prev) => updateAssistant(prev, assistantIndex, (message) => ({
        ...message,
        content: `${message.content}${text}`,
      })));
    });

    try {
      const response = await api.chatQueryStream(
        question,
        target,
        100,
        conversation,
        chatSessionId || undefined,
        chatModel,
      );
      if (!response.ok || !response.body) {
        const err = await response.json().catch(() => ({ error: response.statusText || response.status }));
        throw new Error(err.error || err.detail?.error || response.statusText || "Stream failed");
      }

      await readBiStream(response, (event) => {
        if (event.type === "stage") {
          setChatMessages((prev) => updateAssistant(prev, assistantIndex, (message) => ({
            ...message,
            stages: [...(message.stages || []), event],
          })));
        }
        if (event.type === "sql") {
          setChatMessages((prev) => updateAssistant(prev, assistantIndex, (message) => ({
            ...message,
            data: { ...(message.data || {}), sql: event.sql, intent: event.intent, datasource: event.datasource },
          })));
        }
        if (event.type === "delta" && event.text) {
          deltaBatcher.push(event.text);
        }
        if (event.type === "final" && event.payload) {
          deltaBatcher.flush();
          if (typeof event.payload.sessionId === "string") {
            setChatSessionId(event.payload.sessionId);
          }
          const finalAnswer = String(event.payload.answer || "No answer returned.");
          recordBiFinal(question, target, event.payload, finalAnswer);
          setChatMessages((prev) => updateAssistant(prev, assistantIndex, (message) => ({
            ...message,
            // The backend has already sent the synthesized answer as `delta`
            // events. Do not replace it with a potentially partial final payload.
            content: message.content.trim() ? message.content : finalAnswer,
            data: event.payload,
          })));
        }
      });
    } catch (e) {
      deltaBatcher.flush();
      setChatMessages((prev) => updateAssistant(prev, assistantIndex, (message) => ({
        ...message,
        content: `Error: ${(e as Error).message}`,
        data: { status: "error", question, target },
      })));
    } finally {
      deltaBatcher.flush();
      setChatLoading(false);
    }
  };

  const loadTables = async () => {
    setTablesLoading(true);
    try {
      const data = (await api.getSchema("all")) as { tables?: Record<string, unknown>[] };
      setTables(data.tables || []);
    } catch {
      setTables([]);
    } finally {
      setTablesLoading(false);
    }
  };

  const setMessageChart = (index: number, type: ChartType) => {
    setChatMessages((prev) => prev.map((message, messageIndex) => (
      messageIndex === index ? { ...message, chartOverride: type } : message
    )));
  };

  return (
    <div className="fade-in">
      <div className="tab-bar">
        <button className={`tab-btn ${mode === "sql" ? "active" : ""}`} onClick={() => setMode("sql")}>
          <Terminal size={15} />
          SQL Query
        </button>
        <button className={`tab-btn ${mode === "chat" ? "active" : ""}`} onClick={() => setMode("chat")}>
          <Clipboard size={15} />
          BI Chat
        </button>
        <div style={{ flex: 1 }} />
        {mode === "chat" && (
          <label className="bi-target-control">
            <Cpu size={14} />
            <select value={chatModel} onChange={(event) => setChatModel(event.target.value as AgentModel)} disabled={chatLoading}>
              <option value="model1">Model 1</option>
              <option value="model2">Model 2 Hybrid</option>
            </select>
          </label>
        )}
        {mode === "chat" && (
          <label className="bi-target-control">
            <Database size={14} />
            <select value={chatTarget} onChange={(event) => setChatTarget(event.target.value as Target)}>
              <option value="auto">auto (ClickHouse gold)</option>
              <option value="clickhouse">clickhouse</option>
              <option value="dwh">dwh</option>
            </select>
          </label>
        )}
        <button className="btn btn-secondary" onClick={loadTables} disabled={tablesLoading}>
          <Database size={15} />
          {tablesLoading ? "Loading" : "Schema"}
        </button>
      </div>

      <QueryHistoryPanel
        items={visibleHistory}
        allItems={history}
        filter={historyFilter}
        onFilterChange={setHistoryFilter}
        onReplay={replayHistoryItem}
        onToggleFavorite={toggleFavorite}
        onExport={() => exportHistoryCsv(visibleHistory.length ? visibleHistory : history)}
        onClearRecents={clearRecentHistory}
      />

      {tables !== null && (
        <div className="section">
          <div className="section-title">Schema Catalog</div>
          <div className="schema-grid">
            {tables.map((table, index) => {
              const name = String(table.tableName || "");
              const datasource = String(table.datasource || "dwh");
              return (
                <button
                  key={`${name}-${index}`}
                  className="schema-tile"
                  type="button"
                  onClick={() => {
                    if (datasource === "dwh" && name) {
                      setSql(`SELECT * FROM ${name} LIMIT 20;`);
                      setMode("sql");
                    } else {
                      setChatTarget("clickhouse");
                      setMode("chat");
                    }
                  }}
                >
                  <span className="schema-tile-name">{name || "unknown"}</span>
                  <span className="schema-tile-meta">
                    {datasource} - {String(table.rowCount ?? "?")} rows - {String((table.columns as unknown[])?.length ?? "?")} cols
                  </span>
                </button>
              );
            })}
          </div>
        </div>
      )}

      {mode === "sql" && (
        <div className="section">
          <div className="section-title">SQL Query Editor</div>
          <div className="card">
            <textarea
              className="query-editor"
              value={sql}
              onChange={(e) => setSql(e.target.value)}
              placeholder="SELECT * FROM gold.gold_trace_health_daily LIMIT 20;"
              onKeyDown={(e) => {
                if ((e.ctrlKey || e.metaKey) && e.key === "Enter") runQuery();
              }}
            />
            <div className="query-action-row">
              <button className="btn btn-primary" onClick={() => runQuery()} disabled={sqlLoading || !sql.trim()}>
                <Play size={15} />
                {sqlLoading ? "Running" : "Execute"}
              </button>
              <button className="btn btn-secondary" onClick={explainQuery} disabled={explainLoading || !sql.trim()}>
                <FileSearch size={15} />
                {explainLoading ? "Explaining" : "Explain SQL"}
              </button>
              {result && (
                <span className="query-stat">
                  {result.rowCount} rows - {result.executionTimeMs}ms
                </span>
              )}
            </div>
          </div>

          {sqlError && (
            <div className="query-error">
              {sqlError}
            </div>
          )}

          <ExplainPanel result={explainResult} error={explainError} />

          {result && result.rows && result.rows.length > 0 && (
            <div className="card results-container">
              <ResultTable rows={result.rows} />
            </div>
          )}

          {result && result.rows && result.rows.length === 0 && (
            <div className="empty-state" style={{ marginTop: 16 }}>
              <div className="empty-state-text">Query returned 0 rows.</div>
            </div>
          )}
        </div>
      )}

      {mode === "chat" && (
        <div className="section">
          <div className="section-title">Agentic BI</div>
          <div className="card chat-container bi-chat-container">
            <div className="bi-cost-strip">
              <div className="bi-cost-cell">
                <Calculator size={15} />
                <div>
                  <span>Draft estimate</span>
                  <strong>
                    {draftCost.totalTokens ? `${draftCost.totalTokens} tok - ${formatUsd(draftCost.costUsd)}` : "No prompt"}
                  </strong>
                </div>
              </div>
              <div className="bi-cost-cell">
                <Wallet size={15} />
                <div>
                  <span>Session</span>
                  <strong>{sessionTokens} tok - {formatUsd(sessionCost)}</strong>
                </div>
              </div>
              <button
                className="query-icon-btn"
                type="button"
                title="Reset session cost"
                disabled={!sessionTokens && !sessionCost}
                onClick={resetSessionCost}
              >
                <RefreshCcw size={14} />
              </button>
            </div>
            <div className="chat-messages">
              {chatMessages.length === 0 && (
                <div className="empty-state bi-empty-state">
                  <div className="empty-state-text">No BI conversation yet.</div>
                </div>
              )}
              {chatMessages.map((message, index) => (
                <div key={index} className={`chat-bubble ${message.role}`}>
                  <StageTrail stages={message.stages} />
                  <div>{message.content}</div>
                  {message.role === "assistant" && (
                    <ChatResultPreview
                      data={message.data}
                      chartOverride={message.chartOverride}
                      onChartTypeChange={(type) => setMessageChart(index, type)}
                      onRetry={() => sendChat(String(message.data?.question || ""), (String(message.data?.target || chatTarget) as Target))}
                      onClarificationSelect={(suffix) => {
                        const baseQuestion = String(message.data?.question || "").trim();
                        const clarifiedQuestion = baseQuestion ? `${baseQuestion} ${suffix}` : suffix;
                        sendChat(clarifiedQuestion, (String(message.data?.target || chatTarget) as Target));
                      }}
                      onFollowup={(question) => sendChat(
                        question,
                        (String(message.data?.target || chatTarget) as Target),
                      )}
                    />
                  )}
                </div>
              ))}
              {chatLoading && (
                <div className="chat-bubble assistant">
                  <div className="spinner" style={{ width: 16, height: 16, borderWidth: 2 }} />
                </div>
              )}
              <div ref={scrollRef} />
            </div>
            <div style={{ borderTop: "1px solid rgba(148, 163, 184, 0.18)", display: "flex", flexWrap: "wrap", gap: 10, padding: "12px 14px 0" }}>
              <div style={{ alignItems: "center", color: "#cbd5e1", display: "flex", fontSize: 12, fontWeight: 700, gap: 8, letterSpacing: "0.04em", textTransform: "uppercase" }}>
                <Cpu size={15} />
                Model
              </div>
              <div style={{ background: "rgba(15, 23, 42, 0.9)", border: "1px solid rgba(148, 163, 184, 0.35)", borderRadius: 10, display: "grid", gridTemplateColumns: "1fr 1fr", minWidth: 280, overflow: "hidden", padding: 3 }}>
                <button type="button" onClick={() => setChatModel("model1")} disabled={chatLoading} style={{ background: chatModel === "model1" ? "#f97316" : "transparent", border: 0, borderRadius: 7, color: chatModel === "model1" ? "#fff7ed" : "#94a3b8", cursor: chatLoading ? "not-allowed" : "pointer", fontWeight: 800, minHeight: 34, padding: "0 14px" }}>
                  Model 1
                </button>
                <button type="button" onClick={() => setChatModel("model2")} disabled={chatLoading} style={{ background: chatModel === "model2" ? "#06b6d4" : "transparent", border: 0, borderRadius: 7, color: chatModel === "model2" ? "#ecfeff" : "#94a3b8", cursor: chatLoading ? "not-allowed" : "pointer", fontWeight: 800, minHeight: 34, padding: "0 14px" }}>
                  Model 2 Hybrid
                </button>
              </div>
              <span style={{ alignSelf: "center", color: "#94a3b8", fontSize: 12 }}>
                {chatModel === "model2" ? "MAC-SQL + QueryWeaver hybrid" : "Current production pipeline"}
              </span>
            </div>

            <div className="chat-input-row">
              <input
                className="chat-input"
                placeholder="Ask about traces, logs, LLM cost, tokens, models, or ingestion health..."
                value={chatInput}
                onChange={(e) => setChatInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    sendChat();
                  }
                }}
                disabled={chatLoading}
              />
              <button className="btn btn-primary" onClick={() => sendChat()} disabled={chatLoading || !chatInput.trim()}>
                <Send size={15} />
                Send
              </button>
            </div>
            <AgentFlowGraph
              stages={chatMessages[chatMessages.length - 1]?.stages || []}
              data={chatMessages[chatMessages.length - 1]?.data}
              loading={chatLoading}
            />
          </div>
        </div>
      )}
    </div>
  );
}
