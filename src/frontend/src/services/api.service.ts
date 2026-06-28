const API_BASE = import.meta.env.VITE_OBS_API_URL || "";
const TOKEN_KEY = "agentic-bi.auth.token";

export function getAuthToken(): string {
  return window.localStorage.getItem(TOKEN_KEY) || "";
}

export function setAuthToken(token: string): void {
  if (token) window.localStorage.setItem(TOKEN_KEY, token);
  else window.localStorage.removeItem(TOKEN_KEY);
}

function authHeaders(): Record<string, string> {
  const token = getAuthToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(),
      ...(options?.headers || {}),
    },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({ error: response.statusText }));
    const detail = body.detail;
    const message = typeof detail === "object" && detail
      ? detail.error
      : body.error || body.detail;
    throw new Error(message || response.statusText);
  }
  return response.json();
}

export const api = {
  health: () => request<Record<string, unknown>>("/health"),
  login: (username: string, password: string) =>
    request<Record<string, unknown>>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),
  me: () => request<Record<string, unknown>>("/api/auth/me"),
  overview: () => request<Record<string, unknown>>("/api/dashboard/overview"),
  dataQuality: (limit = 100) =>
    request<Record<string, unknown>>(`/api/dashboard/data-quality?limit=${limit}`),
  metabaseDashboards: () =>
    request<Record<string, unknown>>("/api/dashboard/metabase-dashboards"),

  queryDwh: (sql: string, limit = 100) =>
    request<Record<string, unknown>>("/api/query/dwh", {
      method: "POST",
      body: JSON.stringify({ sql, target: "clickhouse", limit }),
    }),
  explainSql: (sql: string, limit = 100) =>
    request<Record<string, unknown>>("/api/query/explain", {
      method: "POST",
      body: JSON.stringify({ sql, target: "clickhouse", limit }),
    }),
  chatQuery: (
    question: string,
    target = "auto",
    limit = 100,
    conversation: Record<string, unknown>[] = [],
    sessionId?: string,
    model = "model1",
  ) =>
    request<Record<string, unknown>>("/api/query/chat", {
      method: "POST",
      body: JSON.stringify({ question, target, model, limit, conversation, sessionId }),
    }),
  chatQueryStream: (
    question: string,
    target = "auto",
    limit = 100,
    conversation: Record<string, unknown>[] = [],
    sessionId?: string,
    model = "model1",
  ) =>
    fetch(`${API_BASE}/api/query/chat/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({ question, target, model, limit, conversation, sessionId }),
    }),
  generateDashboard: (
    question: string,
    target = "auto",
    limit = 100,
    maxPanels = 3,
    conversation: Record<string, unknown>[] = [],
  ) =>
    request<Record<string, unknown>>("/api/query/dashboard", {
      method: "POST",
      body: JSON.stringify({
        question,
        target,
        limit,
        max_panels: maxPanels,
        conversation,
      }),
    }),
  getSchema: (datasource = "all") =>
    request<Record<string, unknown>>(
      `/api/query/schema?datasource=${encodeURIComponent(datasource)}`,
    ),
  observeChat: (
    question: string,
    conversation: Record<string, unknown>[] = [],
    model = "model1",
  ) =>
    request<Record<string, unknown>>("/api/query/chat", {
      method: "POST",
      body: JSON.stringify({
        question,
        target: "auto",
        model,
        conversation,
      }),
    }),
  observeChatStream: (
    question: string,
    conversation: Record<string, unknown>[] = [],
    sessionId?: string,
    model = "model1",
  ) =>
    fetch(`${API_BASE}/api/monitoring/chat/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({
        question,
        target: "auto",
        model,
        conversation,
        sessionId,
      }),
    }),
  listTables: () => request<Record<string, unknown>>("/api/query/tables"),
  createChatSession: (title = "New conversation") =>
    request<Record<string, unknown>>("/api/query/chat/sessions", {
      method: "POST",
      body: JSON.stringify({ title }),
    }),
  listChatSessions: () =>
    request<Record<string, unknown>>("/api/query/chat/sessions"),
  getChatSession: (sessionId: string) =>
    request<Record<string, unknown>>(`/api/query/chat/sessions/${encodeURIComponent(sessionId)}`),
};
