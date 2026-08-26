import type { EscalationOut, SubtaskOut, TaskMessageOut, TraceSpanOut } from "./api";

// Turns the flat, chronological TraceSpan list (GET /v1/tasks/{id}/trace)
// into feed messages. One span = one message, except: tool_selection spans
// never render standalone -- their `rationale` is folded into the very next
// tool_call/reasoning span for the same subtask (mirrors how the mockup
// shows a short dim sentence above each tool card), and raw "escalation"
// spans are dropped in favor of the richer Escalation rows from
// GET /v1/escalations (which carry status/decision, needed for the
// approve/take_over/reject gate). TaskMessage rows (user follow-ups sent
// after the task first completed, see POST /v1/tasks/{id}/messages) are
// merged in by timestamp alongside everything else.

export type FeedItemKind =
  | "sketch"
  | "agent_step"
  | "tool_call"
  | "reasoning"
  | "review"
  | "escalation"
  | "synthesize"
  | "user_message";

export interface FeedItem {
  key: string;
  timestamp: string;
  kind: FeedItemKind;
  span?: TraceSpanOut;
  escalation?: EscalationOut;
  message?: TaskMessageOut;
  subtask?: SubtaskOut;
  rationale?: string;
}

export function buildFeed(
  spans: TraceSpanOut[],
  subtasks: SubtaskOut[],
  escalations: EscalationOut[],
  messages: TaskMessageOut[] = []
): FeedItem[] {
  const bySubtaskId = new Map(subtasks.map((s) => [s.id, s]));
  const pendingRationale = new Map<string, string>();
  const items: FeedItem[] = [];

  for (const span of spans) {
    const subtask = span.subtask_id ? bySubtaskId.get(span.subtask_id) : undefined;

    if (span.span_type === "tool_selection") {
      const rationale = typeof span.output?.rationale === "string" ? (span.output.rationale as string) : undefined;
      if (span.subtask_id && rationale) pendingRationale.set(span.subtask_id, rationale);
      continue;
    }
    if (span.span_type === "escalation") continue;

    if (span.span_type === "tool_call" || span.span_type === "reasoning") {
      const rationale = span.subtask_id ? pendingRationale.get(span.subtask_id) : undefined;
      if (span.subtask_id) pendingRationale.delete(span.subtask_id);
      items.push({ key: span.id, timestamp: span.started_at, kind: span.span_type, span, subtask, rationale });
      continue;
    }

    if (span.span_type === "sketch" || span.span_type === "agent_step" || span.span_type === "review" || span.span_type === "synthesize") {
      items.push({ key: span.id, timestamp: span.started_at, kind: span.span_type, span, subtask });
    }
  }

  for (const esc of escalations) {
    const subtask = esc.subtask_id ? bySubtaskId.get(esc.subtask_id) : undefined;
    items.push({ key: esc.id, timestamp: esc.created_at, kind: "escalation", escalation: esc, subtask });
  }

  for (const msg of messages) {
    items.push({ key: msg.id, timestamp: msg.created_at, kind: "user_message", message: msg });
  }

  items.sort((a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime());
  return items;
}

export function spanDurationLabel(span: TraceSpanOut): string {
  if (!span.ended_at) return "running…";
  const ms = new Date(span.ended_at).getTime() - new Date(span.started_at).getTime();
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`;
}
