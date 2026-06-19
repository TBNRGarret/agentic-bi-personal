import { useEffect, useRef, useState } from "react";
import { Send, Sparkles } from "lucide-react";
import { api } from "../../services/api.service";
import { DynamicChart, type ChartSpec } from "../Charts/DynamicChart";
import { MarkdownRenderer } from "./MarkdownRenderer";

interface ObserveChartSpec extends ChartSpec {
  id?: string;
  source?: string;
  color?: string;
  rows?: Record<string, unknown>[];
  visualizationDsl?: Record<string, unknown>;
}

interface ObserveChatResponse {
  answer?: string;
  agent?: string;
  intent?: string;
  status?: string;
  executionTimeMs?: number;
  rows?: Record<string, unknown>[];
  metrics?: Record<string, unknown>;
  question?: string;
  chartSpec?: ObserveChartSpec;
  chartSpecs?: ObserveChartSpec[];
  visualizationDsl?: Record<string, unknown>;
  forecast?: Record<string, unknown>;
  toolDecision?: Record<string, unknown>;
  knowledge?: Record<string, unknown>;
  pythonAnalysis?: Record<string, unknown>;
  clarification?: ClarificationPayload;
  sql?: string;
  target?: string;
  rowCount?: number;
  sessionId?: string;
  suggestions?: string[];
  selectedTables?: string[];
  searchedTables?: string[];
  routeNote?: string;
  tableSelection?: {
    primaryTable?: string;
    requiredCapabilities?: string[];
    candidates?: Array<{
      tableName?: string;
      score?: number;
      reasons?: string[];
    }>;
  };
  llmUsage?: {
    totalTokens?: number;
    estimatedCostUsd?: number;
    model?: string;
  };
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

interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  data?: ObserveChatResponse;
}

interface ChatSession {
  session_id: string;
  title: string;
  updated_at?: string;
  messages?: Array<{
    role: "user" | "assistant";
    content: string;
    payload?: ObserveChatResponse;
  }>;
}

interface StreamEvent {
  type?: "stage" | "delta" | "final";
  stage?: string;
  status?: "info" | "success" | "error";
  text?: string;
  message?: string;
  payload?: ObserveChatResponse;
}

interface ChartView {
  id: string;
  chartSpec: ObserveChartSpec;
  rows: Record<string, unknown>[];
  visualizationDsl?: Record<string, unknown>;
}

const SUGGESTED_PROMPTS = [
  "Tong quan observe platform 24h qua the nao?",
  "Co service nao dang loi hoac alert khong?",
  "Vi sao latency backend tang cao?",
  "Can lam gi tiep theo de giam rui ro?",
];

const CHART_TYPES = ["table", "bar", "line", "pie", "scatter", "histogram", "box", "heatmap"] as const;

function updateAssistant(
  messages: ChatMessage[],
  index: number,
  updater: (message: ChatMessage) => ChatMessage,
) {
  return messages.map((message, messageIndex) => (messageIndex === index ? updater(message) : message));
}

function getRows(data?: ObserveChatResponse): Record<string, unknown>[] {
  return Array.isArray(data?.rows) ? data.rows : [];
}

function getChartSpec(data?: ObserveChatResponse): ObserveChartSpec {
  const base = data?.chartSpec;
  if (base) return cleanChartSpec(base);

  const dsl = data?.visualizationDsl;
  const config = typeof dsl?.config === "object" && dsl.config ? (dsl.config as Record<string, unknown>) : null;
  const layout = typeof dsl?.layout === "object" && dsl.layout ? (dsl.layout as Record<string, unknown>) : null;
  const chartSpec = {
    type: chartTypeValue(dsl?.chart_type) || "table",
    x: stringValue(config?.x) || stringValue(config?.labels),
    y: stringListValue(config?.y) || stringValue(config?.values),
    title: stringValue(layout?.title),
  };
  return cleanChartSpec(chartSpec);
}

function getChartViews(data?: ObserveChatResponse, question = ""): ChartView[] {
  if (!data) return [];
  const views: ChartView[] = [];
  const primaryRows = getRows(data);
  const primarySpec = getChartSpec(data);
  if (chartMatchesRows(primarySpec, primaryRows, question || data.question || "")) {
    views.push({
      id: "primary-chart",
      chartSpec: primarySpec,
      rows: primaryRows,
      visualizationDsl: data.chartSpec ? undefined : data.visualizationDsl,
    });
  }

  const specs = Array.isArray(data.chartSpecs) ? data.chartSpecs : [];
  specs.forEach((spec, index) => {
    if (!spec || typeof spec !== "object") return;
    const chartSpec = cleanChartSpec({
      ...spec,
      type: chartTypeValue(spec.type) || "table",
    });
    const rows = arrayRows(spec.rows) || rowsForChartSource(data.metrics, spec.source);
    if (!chartMatchesRows(chartSpec, rows, question || data.question || "")) return;
    views.push({
      id: spec.id || `${spec.source || "observe-chart"}-${index}`,
      chartSpec,
      rows,
      visualizationDsl: spec.type ? undefined : spec.visualizationDsl,
    });
  });

  return views.slice(0, 4);
}

function chartMatchesRows(
  chartSpec: ObserveChartSpec,
  rows: Record<string, unknown>[],
  question: string,
): boolean {
  if (rows.length < 2 || chartSpec.type === "table") return false;
  const columns = new Set(rows.flatMap((row) => Object.keys(row)));
  const yColumns = Array.isArray(chartSpec.y) ? chartSpec.y : chartSpec.y ? [chartSpec.y] : [];
  const seriesColumns = Array.isArray(chartSpec.series) ? chartSpec.series : chartSpec.series ? [chartSpec.series] : [];
  if (chartSpec.x && !columns.has(chartSpec.x)) return false;
  if (seriesColumns.some((column) => !columns.has(column))) return false;
  if (yColumns.length === 0 || !yColumns.some((column) => columns.has(column))) return false;
  if (asksForLatency(question) && !yColumns.some(isLatencyColumn)) return false;
  return true;
}

function asksForLatency(question: string): boolean {
  const normalized = question.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
  return normalized.includes("latency") || normalized.includes("do tre") || normalized.includes("duration");
}

function isLatencyColumn(column: string): boolean {
  const normalized = column.toLowerCase();
  return normalized.includes("latency") || normalized.includes("duration");
}

function stringValue(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value : undefined;
}

function stringListValue(value: unknown): string[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const values = value.filter((item): item is string => typeof item === "string" && Boolean(item.trim()));
  return values.length ? values : undefined;
}

function chartTypeValue(value: unknown): ChartSpec["type"] | undefined {
  if (typeof value !== "string") return undefined;
  return CHART_TYPES.includes(value as (typeof CHART_TYPES)[number]) ? value as ChartSpec["type"] : undefined;
}

function cleanChartSpec(spec: ObserveChartSpec): ObserveChartSpec {
  const reason = stringValue(spec.reason);
  if (!reason || !/visualization llm failed|openrouter|server error|service unavailable/i.test(reason)) {
    return spec;
  }
  const { reason: _reason, ...cleaned } = spec;
  return cleaned;
}

function arrayRows(value: unknown): Record<string, unknown>[] | null {
  if (Array.isArray(value)) {
    const rows = value.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object" && !Array.isArray(item));
    return rows.length ? rows : null;
  }
  if (value && typeof value === "object") {
    const record = value as Record<string, unknown>;
    return arrayRows(record.rows) || arrayRows(record.data);
  }
  return null;
}

function rowsForChartSource(metrics: unknown, source?: string): Record<string, unknown>[] {
  if (!source || !metrics || typeof metrics !== "object") return [];
  return arrayRows((metrics as Record<string, unknown>)[source]) || [];
}

function getClarification(data?: ObserveChatResponse): ClarificationPayload | null {
  const clarification = data?.clarification;
  if (!clarification || !Array.isArray(clarification.options) || clarification.options.length === 0) {
    return null;
  }
  return clarification;
}

function buildObserveConversation(messages: ChatMessage[]): Record<string, unknown>[] {
  return messages
    .slice(-12)
    .filter((message) => message.content.trim() || message.data)
    .map((message) => {
      const data = message.data || {};
      const compactData: Record<string, unknown> = {};
      ["sql", "target", "status", "rowCount", "intent", "agent"].forEach((key) => {
        const value = (data as Record<string, unknown>)[key];
        if (value !== undefined && value !== null && value !== "") {
          compactData[key] = value;
        }
      });
      if (data.chartSpec) {
        compactData.chartSpec = data.chartSpec;
      }
      if (Array.isArray(data.chartSpecs) && data.chartSpecs.length) {
        compactData.chartSpecs = data.chartSpecs.slice(0, 4).map(({ id, source, title, type, x, y, series }) => ({
          id,
          source,
          title,
          type,
          x,
          y,
          series,
        }));
      }
      return { role: message.role, content: message.content, data: compactData };
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

function ClarificationPanel({
  data,
  onSelect,
}: {
  data?: ObserveChatResponse;
  onSelect: (questionSuffix: string) => void;
}) {
  const clarification = getClarification(data);
  if (!clarification) return null;
  return (
    <div className="observe-clarification-panel">
      <div className="observe-clarification-title">{clarification.question || "Clarify request"}</div>
      <div className="observe-clarification-options">
        {clarification.options?.map((option) => {
          const suffix = option.questionSuffix || option.value || option.label || "";
          return (
            <button
              key={option.value || option.label || suffix}
              type="button"
              className="btn btn-secondary observe-clarification-option"
              onClick={() => suffix && onSelect(suffix)}
              disabled={!suffix}
            >
              <span>{option.label || option.value || "Option"}</span>
              {option.description && <small>{option.description}</small>}
            </button>
          );
        })}
      </div>
    </div>
  );
}

async function readObserveStream(response: Response, onEvent: (event: StreamEvent) => void) {
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

export function ObserveChatPanel() {
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [progressMessage, setProgressMessage] = useState("Dang phan tich telemetry...");
  const [loading, setLoading] = useState(false);
  const [windowHours, setWindowHours] = useState(24);
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [sessionId, setSessionId] = useState("");
  const scrollRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    api.listChatSessions()
      .then(async (response) => {
        const items = Array.isArray(response.sessions) ? response.sessions as unknown as ChatSession[] : [];
        setSessions(items);
        if (items[0]?.session_id) {
          await openSession(items[0].session_id);
        }
      })
      .catch(() => setSessions([]));
  }, []);

  useEffect(() => {
    scrollRef.current?.scrollIntoView({
      behavior: loading ? "auto" : "smooth",
      block: "end",
    });
  }, [messages.length, loading]);

  const sendChat = async (override?: string) => {
    const question = (override || input).trim();
    if (!question || loading) return;

    const conversation = buildObserveConversation(messages);
    let activeSessionId = sessionId;
    if (!activeSessionId) {
      const created = await api.createChatSession(question.slice(0, 120));
      const session = created.session as unknown as ChatSession;
      activeSessionId = session.session_id;
      setSessionId(activeSessionId);
      setSessions((previous) => [session, ...previous]);
    }
    setInput("");
    const assistantIndex = messages.length + 1;
    setMessages((prev) => [
      ...prev,
      { role: "user", content: question },
      { role: "assistant", content: "" },
    ]);
    setProgressMessage("Dang phan tich telemetry...");
    setLoading(true);
    const deltaBatcher = createDeltaBatcher((text) => {
      setMessages((prev) => updateAssistant(prev, assistantIndex, (message) => ({
        ...message,
        content: `${message.content}${text}`,
      })));
    });

    try {
      const response = await api.observeChatStream(
        question,
        windowHours,
        conversation,
        activeSessionId,
      );
      if (!response.ok || !response.body) {
        throw new Error(`Stream failed: ${response.statusText || response.status}`);
      }

      await readObserveStream(response, (event) => {
        if (event.type === "stage") {
          setProgressMessage(event.message || `Dang xu ly ${event.stage || "yeu cau"}...`);
        }
        if (event.type === "delta" && event.text) {
          deltaBatcher.push(event.text);
        }
        if (event.type === "final" && event.payload) {
          deltaBatcher.flush();
          if (event.payload.sessionId) setSessionId(event.payload.sessionId);
          setMessages((prev) => updateAssistant(prev, assistantIndex, (message) => ({
            ...message,
            content: event.payload?.answer || message.content || "Khong co cau tra loi.",
            data: event.payload,
          })));
        }
      });
    } catch (error) {
      deltaBatcher.flush();
      setMessages((prev) => updateAssistant(prev, assistantIndex, (message) => ({
        ...message,
        content: `Error: ${(error as Error).message}`,
      })));
    } finally {
      deltaBatcher.flush();
      setLoading(false);
      setProgressMessage("Dang phan tich telemetry...");
    }
  };

  const openSession = async (selectedSessionId: string) => {
    const response = await api.getChatSession(selectedSessionId);
    const session = response.session as unknown as ChatSession;
    setSessionId(session.session_id);
    setMessages((session.messages || []).map((message) => ({
      role: message.role,
      content: message.content,
      data: message.role === "assistant" ? message.payload : undefined,
    })));
  };

  const newSession = async () => {
    const response = await api.createChatSession();
    const session = response.session as unknown as ChatSession;
    setSessions((previous) => [session, ...previous]);
    setSessionId(session.session_id);
    setMessages([]);
  };

  return (
    <div className="fade-in observe-chat-page">
      <div className="observe-chat-shell">
        <div className="observe-chat-main">
          <div className="observe-chat-toolbar">
            <div className="observe-chat-mode">
              <Sparkles size={16} />
              <span>Monitoring chat</span>
            </div>
            <label className="observe-window-control">
              <span>Window</span>
              <select value={windowHours} onChange={(event) => setWindowHours(Number(event.target.value))}>
                <option value={1}>1h</option>
                <option value={6}>6h</option>
                <option value={24}>24h</option>
                <option value={72}>72h</option>
                <option value={168}>7d</option>
              </select>
            </label>
            <label className="observe-window-control">
              <span>Session</span>
              <select value={sessionId} onChange={(event) => openSession(event.target.value)}>
                <option value="">New conversation</option>
                {sessions.map((session) => (
                  <option key={session.session_id} value={session.session_id}>{session.title}</option>
                ))}
              </select>
            </label>
            <button className="btn btn-secondary" type="button" onClick={newSession}>New</button>
          </div>

          <div className="observe-chat-messages">
            {messages.length === 0 && (
              <div className="observe-empty">
                <Sparkles size={32} />
                <div className="observe-empty-title">Ask the platform</div>
                <div className="observe-prompt-row">
                  {SUGGESTED_PROMPTS.map((prompt) => (
                    <button
                      key={prompt}
                      className="observe-prompt"
                      type="button"
                      onClick={() => sendChat(prompt)}
                      disabled={loading}
                    >
                      {prompt}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {messages.map((message, index) => (
              <div key={`${message.role}-${index}`} className={`observe-message ${message.role}`}>
                <div className="observe-message-content">
                  {message.role === "assistant" ? (
                    <MarkdownRenderer content={message.content} />
                  ) : (
                    message.content
                  )}
                </div>
                {message.role === "assistant" && (
                  <ClarificationPanel
                    data={message.data}
                    onSelect={(suffix) => {
                      const previousUser = messages[index - 1]?.role === "user" ? messages[index - 1]?.content : "";
                      const baseQuestion = String(message.data?.question || previousUser || "").trim();
                      const clarified = baseQuestion ? `${baseQuestion} ${suffix}` : suffix;
                      sendChat(clarified);
                    }}
                  />
                )}
                {message.role === "assistant" && getChartViews(
                  message.data,
                  messages[index - 1]?.role === "user" ? messages[index - 1].content : "",
                ).map((chart) => (
                  <DynamicChart
                    key={chart.id}
                    chartSpec={chart.chartSpec}
                    rows={chart.rows}
                    visualizationDsl={chart.visualizationDsl}
                  />
                ))}
                {message.role === "assistant" && message.data?.routeNote && (
                  <div className="observe-route-note">{message.data.routeNote}</div>
                )}
                {message.role === "assistant" && message.data?.llmUsage && (
                  <div className="observe-cost-badge">
                    {Number(message.data.llmUsage.totalTokens || 0).toLocaleString()} tokens
                    {" / "}
                    ${Number(message.data.llmUsage.estimatedCostUsd || 0).toFixed(6)}
                  </div>
                )}
                {message.role === "assistant" && Array.isArray(message.data?.selectedTables) && message.data.selectedTables.length > 0 && (
                  <details className="observe-data-sources">
                    <summary>
                      Data used: {message.data.selectedTables.join(", ")}
                    </summary>
                    <div className="observe-data-source-details">
                      {(message.data.tableSelection?.requiredCapabilities || []).length > 0 && (
                        <div>
                          Required: {message.data.tableSelection?.requiredCapabilities?.join(", ")}
                        </div>
                      )}
                      {(message.data.tableSelection?.candidates || []).map((candidate) => (
                        <div key={candidate.tableName}>
                          <strong>{candidate.tableName}</strong>
                          {" "}
                          score {candidate.score ?? 0}
                          {Array.isArray(candidate.reasons) && candidate.reasons.length > 0
                            ? ` - ${candidate.reasons.join("; ")}`
                            : ""}
                        </div>
                      ))}
                    </div>
                  </details>
                )}
                {message.role === "assistant" && Array.isArray(message.data?.suggestions) && (
                  <div className="observe-followups">
                    {message.data.suggestions.map((suggestion) => (
                      <button key={suggestion} type="button" onClick={() => sendChat(suggestion)} disabled={loading}>
                        {suggestion}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            ))}

            {loading && (
              <div className="observe-message assistant">
                <div className="observe-loading">
                  <div className="spinner" />
                  <span>{progressMessage}</span>
                </div>
              </div>
            )}
            <div ref={scrollRef} />
          </div>

          <div className="observe-input-row">
            <textarea
              className="observe-chat-input"
              placeholder="Hoi ve loi, latency, alert, trace, log hoac suc khoe he thong..."
              value={input}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  sendChat();
                }
              }}
              disabled={loading}
              rows={2}
            />
            <button className="btn btn-primary observe-send-btn" onClick={() => sendChat()} disabled={loading || !input.trim()}>
              <Send size={16} />
              Send
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
