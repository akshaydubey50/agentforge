// Hand-mirrored from src/agentsys/schemas.py. Kept in sync by hand -- the
// surface is small enough that shared codegen isn't worth it for this slice.

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8100";

export type TaskStatus =
  | "pending"
  | "running"
  | "awaiting_approval"
  | "completed"
  | "failed";

export type SubtaskStatus =
  | "pending"
  | "ready"
  | "running"
  | "needs_revision"
  | "done"
  | "escalated"
  | "failed"
  | "skipped";

export interface SubtaskOut {
  id: string;
  position: number;
  description: string;
  depends_on: string[];
  assigned_tool: string | null;
  status: SubtaskStatus;
  output: string | null;
  attempt_count: number;
  created_at: string;
}

export interface TaskOut {
  id: string;
  request_text: string;
  status: TaskStatus;
  final_output: string | null;
  created_at: string;
  updated_at: string;
}

export interface TaskMessageOut {
  id: string;
  role: string;
  content: string;
  created_at: string;
}

export interface TaskDetailOut extends TaskOut {
  subtasks: SubtaskOut[];
  messages: TaskMessageOut[];
}

export interface EscalationOut {
  id: string;
  task_id: string;
  subtask_id: string | null;
  reason: string;
  status: "pending" | "approved" | "rejected" | "took_over";
  decision_note: string | null;
  decided_by: string | null;
  created_at: string;
  decided_at: string | null;
}

export type EscalationDecision = "approve" | "reject" | "take_over";

export interface TraceSpanOut {
  id: string;
  subtask_id: string | null;
  span_type: string;
  name: string;
  input: Record<string, unknown>;
  output: Record<string, unknown>;
  status: string;
  started_at: string;
  ended_at: string | null;
}

export interface Page<T> {
  items: T[];
  total: number;
}

export interface ToolInfo {
  name: string;
  description: string;
}

export interface UserOut {
  id: string;
  email: string;
  name: string | null;
  picture_url: string | null;
}

export interface GoogleConnectionOut {
  connected: boolean;
  configured: boolean;
  google_email: string | null;
  scopes: string[];
  connected_at: string | null;
}

export interface MemoryEntryOut {
  id: string;
  task_id: string | null;
  kind: string;
  content: string;
  importance: number;
  created_at: string;
  last_accessed_at: string;
}

export interface AnalyticsOut {
  tasks_by_status: Record<string, number>;
  tool_stats: Record<
    string,
    { calls: number; successes: number; total_latency_ms: number; success_rate: number | null; avg_latency_ms: number | null }
  >;
  escalations_by_status: Record<string, number>;
  total_tasks: number;
  total_tool_calls: number;
  total_cost_usd: number;
  cost_by_purpose: Record<string, number>;
}

// --- System view (mirrors src/agentsys/system_api.py) -----------------------
// The node/edge lists come from the COMPILED LangGraph, not a hand-kept list,
// so anything drawn from them stays true as the graph changes. See that
// module's docstring for the honesty rule this depends on.

export type AgentRoleName = "supervisor" | "specialist" | "reviewer" | "human";

export interface TopologyNode {
  id: string;
  label: string;
  role: AgentRoleName;
  kind: string;
  summary: string;
  href: string | null;
  /** Which TraceSpan.span_type this node writes, for looking up live
   *  activity in SystemSummary.activity_24h. Null for the terminals. */
  span_type: string | null;
  terminal: boolean;
}

export interface TopologyEdge {
  source: string;
  target: string;
  /** A router decision (route_entry / route_after) rather than an
   *  unconditional hand-off -- drawn dashed. */
  conditional: boolean;
}

export interface SubsystemFact {
  label: string;
  value: string;
  note?: string;
}

export interface Subsystem {
  id: string;
  label: string;
  summary: string;
  /** Null for subsystems with no page to send you to — deployment config
   *  read from .env at startup, which the System page already shows in full. */
  href: string | null;
  facts: SubsystemFact[];
}

export interface SystemTool {
  name: string;
  summary: string;
  requires_approval: boolean;
  /** The MCP server this tool came from, or null for a first-party tool. */
  server: string | null;
}

export interface SystemTopology {
  graph: { nodes: TopologyNode[]; edges: TopologyEdge[] };
  tools: SystemTool[];
  subsystems: Subsystem[];
}

export interface SystemSummary {
  tasks: {
    by_status: Record<string, number>;
    total: number;
    active: number;
    awaiting_approval: number;
  };
  approvals_pending: number;
  memory_entries: number;
  tools_registered: number;
  steps_run: number;
  tool_calls: { total: number; failed: number };
  spend: { usd: number; llm_calls: number; tokens_in: number; tokens_out: number };
  trace_spans_24h: number;
  /** Keyed by TraceSpan.span_type -- join to a node via its `span_type`. */
  activity_24h: Record<string, { runs: number; errors: number }>;
}

// A 401 here means the session cookie is missing/expired -- every caller
// bounces to /login instead of each page having to check response.status
// itself. window.location (not next/navigation's router) because api.ts is
// a plain module, not a component -- it has no router instance to call.
function redirectToLogin() {
  if (typeof window !== "undefined") window.location.href = "/login";
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    credentials: "include",
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (res.status === 401) {
    redirectToLogin();
    throw new Error("not authenticated");
  }
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`${init?.method || "GET"} ${path} failed: ${res.status} ${body}`);
  }
  return res.json() as Promise<T>;
}

export const ATTACHABLE_EXTENSIONS = [".txt", ".md", ".csv", ".json", ".py", ".log", ".yaml", ".yml"];

export const api = {
  createTask: (requestText: string) =>
    request<TaskOut>("/v1/tasks", {
      method: "POST",
      body: JSON.stringify({ request_text: requestText }),
    }),

  createTaskWithFiles: async (requestText: string, files: File[]): Promise<TaskOut> => {
    const form = new FormData();
    form.append("request_text", requestText);
    for (const file of files) form.append("files", file);
    const res = await fetch(`${API_BASE_URL}/v1/tasks/upload`, {
      method: "POST",
      body: form,
      credentials: "include",
    });
    if (res.status === 401) {
      redirectToLogin();
      throw new Error("not authenticated");
    }
    if (!res.ok) {
      const body = await res.text().catch(() => "");
      throw new Error(`POST /v1/tasks/upload failed: ${res.status} ${body}`);
    }
    return res.json() as Promise<TaskOut>;
  },

  listTasks: (limit = 25, offset = 0, status: TaskStatus | "all" = "all") =>
    request<Page<TaskOut>>(`/v1/tasks?status=${status}&limit=${limit}&offset=${offset}`),

  getTask: (taskId: string) => request<TaskDetailOut>(`/v1/tasks/${taskId}`),

  sendTaskMessage: (taskId: string, content: string) =>
    request<TaskOut>(`/v1/tasks/${taskId}/messages`, {
      method: "POST",
      body: JSON.stringify({ content }),
    }),

  getTrace: (taskId: string) => request<TraceSpanOut[]>(`/v1/tasks/${taskId}/trace`),

  listEscalations: (status: "pending" | "all" = "pending", limit = 25, offset = 0, taskId?: string) =>
    request<Page<EscalationOut>>(
      `/v1/escalations?status=${status}&limit=${limit}&offset=${offset}${taskId ? `&task_id=${taskId}` : ""}`
    ),

  countPendingEscalations: () =>
    request<Page<EscalationOut>>(`/v1/escalations?status=pending&limit=1&offset=0`).then((p) => p.total),

  decideEscalation: (
    escalationId: string,
    decision: EscalationDecision,
    opts?: { note?: string; decidedBy?: string; overrideOutput?: string }
  ) =>
    request<EscalationOut>(`/v1/escalations/${escalationId}/decide`, {
      method: "POST",
      body: JSON.stringify({
        decision,
        note: opts?.note ?? "",
        decided_by: opts?.decidedBy ?? "human",
        override_output: opts?.overrideOutput ?? null,
      }),
    }),

  listTools: () => request<{ tools: ToolInfo[] }>("/v1/tools").then((r) => r.tools),

  // Describes code and config, not this user's data, so it's safe to cache
  // hard -- the System page revalidates only /summary on a timer.
  getSystemTopology: () => request<SystemTopology>("/v1/system/topology"),

  getSystemSummary: () => request<SystemSummary>("/v1/system/summary"),

  getAnalytics: () => request<AnalyticsOut>("/v1/analytics"),

  listMemory: (kind = "all", limit = 25, offset = 0) =>
    request<Page<MemoryEntryOut>>(`/v1/memory?kind=${kind}&limit=${limit}&offset=${offset}`),

  getGoogleStatus: () => request<GoogleConnectionOut>("/v1/integrations/google/status"),

  disconnectGoogle: () =>
    request<GoogleConnectionOut>("/v1/integrations/google/disconnect", { method: "POST" }),

  getMe: () => request<UserOut>("/v1/auth/me"),

  logout: () => request<{ ok: boolean }>("/v1/auth/logout", { method: "POST" }),
};

// Sign-in AND (re)connecting Drive/Gmail are the same OAuth grant (see
// google_oauth.py's login_or_connect) -- this one URL serves both the
// /login page's "Sign in with Google" button and the Integrations page's
// "Reconnect" affordance (which passes next="/integrations" to land back
// there instead of the dashboard root). Must be a full-page browser
// navigation, NOT an api-client fetch -- Google's consent screen can't
// render inside XHR/CORS. The frontend points window.location / an
// <a href> at this.
export const googleLoginUrl = (next = "/") =>
  `${API_BASE_URL}/v1/auth/google/login?next=${encodeURIComponent(next)}`;

export const isActiveStatus = (status: TaskStatus) =>
  status === "pending" || status === "running";
