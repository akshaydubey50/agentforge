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
}

export interface TaskOut {
  id: string;
  request_text: string;
  status: TaskStatus;
  final_output: string | null;
  created_at: string;
  updated_at: string;
}

export interface TaskDetailOut extends TaskOut {
  subtasks: SubtaskOut[];
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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
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
    const res = await fetch(`${API_BASE_URL}/v1/tasks/upload`, { method: "POST", body: form });
    if (!res.ok) {
      const body = await res.text().catch(() => "");
      throw new Error(`POST /v1/tasks/upload failed: ${res.status} ${body}`);
    }
    return res.json() as Promise<TaskOut>;
  },

  listTasks: (limit = 25, offset = 0, status: TaskStatus | "all" = "all") =>
    request<Page<TaskOut>>(`/v1/tasks?status=${status}&limit=${limit}&offset=${offset}`),

  getTask: (taskId: string) => request<TaskDetailOut>(`/v1/tasks/${taskId}`),

  getTrace: (taskId: string) => request<TraceSpanOut[]>(`/v1/tasks/${taskId}/trace`),

  listEscalations: (status: "pending" | "all" = "pending", limit = 25, offset = 0) =>
    request<Page<EscalationOut>>(`/v1/escalations?status=${status}&limit=${limit}&offset=${offset}`),

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

  getAnalytics: () => request<AnalyticsOut>("/v1/analytics"),
};

export const isActiveStatus = (status: TaskStatus) =>
  status === "pending" || status === "running";
