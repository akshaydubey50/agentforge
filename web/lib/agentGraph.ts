import type { EscalationOut, SubtaskOut, TaskDetailOut, TraceSpanOut } from "./api";
import type { AgentRole } from "./agentRoles";

export type GraphNodeKind = "sketch" | "execute" | "review" | "escalation" | "synthesize";
export type StatusTone = "done" | "active" | "esc" | "pending" | "failed";

export interface GraphNodeData {
  // Index signature: @xyflow/react's Node<T> requires node data to satisfy
  // Record<string, unknown> so it can pass data through generically.
  [key: string]: unknown;
  id: string;
  kind: GraphNodeKind;
  lane: string;
  title: string;
  subtitle?: string;
  role: AgentRole;
  statusLabel: string;
  tone: StatusTone;
  x: number;
  y: number;
  subtask?: SubtaskOut;
  spans: TraceSpanOut[];
  escalation?: EscalationOut;
}

export interface GraphEdgeData {
  id: string;
  from: string;
  to: string;
  tone: StatusTone;
  dashed?: boolean;
}

const COL = { supervisor: 40, specialist: 320, reviewer: 600, human: 880, synthesize: 1160 };
const ROW_H = 190;
const ROW0 = 40;

function subtaskTone(s: SubtaskOut): StatusTone {
  switch (s.status) {
    case "done":
      return "done";
    case "running":
      return "active";
    case "escalated":
      return "esc";
    case "failed":
      return "failed";
    default:
      return "pending";
  }
}

export function buildGraph(
  task: TaskDetailOut,
  spans: TraceSpanOut[],
  escalations: EscalationOut[]
): { nodes: GraphNodeData[]; edges: GraphEdgeData[] } {
  const nodes: GraphNodeData[] = [];
  const edges: GraphEdgeData[] = [];
  const subtasks = [...task.subtasks].sort((a, b) => a.position - b.position);
  const spansBySubtask = new Map<string, TraceSpanOut[]>();
  for (const s of spans) {
    if (!s.subtask_id) continue;
    const list = spansBySubtask.get(s.subtask_id) ?? [];
    list.push(s);
    spansBySubtask.set(s.subtask_id, list);
  }

  const sketchSpan = spans.find((s) => s.span_type === "sketch");
  const sketchOut = sketchSpan?.output as { outline?: string[]; confidence?: number } | undefined;
  nodes.push({
    id: "sketch",
    kind: "sketch",
    lane: "supervisor · sketch",
    title: "Supervisor",
    subtitle: sketchOut ? `${sketchOut.outline?.length ?? 0} outline steps · ${sketchOut.confidence}/5` : undefined,
    role: "supervisor",
    statusLabel: sketchSpan ? "✓ sketched" : "pending",
    tone: sketchSpan ? "done" : "pending",
    x: COL.supervisor,
    y: ROW0,
    spans: sketchSpan ? [sketchSpan] : [],
  });

  let prevRightId = "sketch";
  let lastRow = 0;

  subtasks.forEach((s, i) => {
    const y = ROW0 + i * ROW_H;
    const mySpans = spansBySubtask.get(s.id) ?? [];
    const toolCall = [...mySpans].reverse().find((sp) => sp.span_type === "tool_call");
    const reasoning = [...mySpans].reverse().find((sp) => sp.span_type === "reasoning");
    const review = [...mySpans].reverse().find((sp) => sp.span_type === "review");
    const tone = subtaskTone(s);

    const execId = `exec-${s.id}`;
    nodes.push({
      id: execId,
      kind: "execute",
      lane: `subtask #${s.position}`,
      title: s.description.length > 42 ? s.description.slice(0, 42) + "…" : s.description,
      subtitle: s.assigned_tool ?? "reasoning",
      role: "specialist",
      statusLabel:
        tone === "done"
          ? `✓ done${toolCall ? ` · ${durationLabel(toolCall)}` : ""}`
          : tone === "active"
            ? "● running"
            : tone === "esc"
              ? "⏸ escalated"
              : tone === "failed"
                ? "✕ failed"
                : "pending",
      tone,
      x: COL.specialist,
      y,
      subtask: s,
      spans: [toolCall, reasoning].filter((x): x is TraceSpanOut => !!x),
    });
    edges.push({ id: `${prevRightId}-${execId}`, from: prevRightId, to: execId, tone: tone === "pending" ? "pending" : "done" });

    if (review) {
      const reviewOut = review.output as { score?: number; verdict?: string };
      const reviewId = `review-${s.id}`;
      const reviewTone: StatusTone = reviewOut.verdict === "pass" ? "done" : reviewOut.verdict === "reject" ? "active" : "esc";
      nodes.push({
        id: reviewId,
        kind: "review",
        lane: `review #${s.position}`,
        title: "Reviewer",
        subtitle: undefined,
        role: "reviewer",
        statusLabel: reviewOut.verdict === "pass" ? `✓ pass · ${reviewOut.score}/5` : `${reviewOut.verdict} · ${reviewOut.score}/5`,
        tone: reviewTone,
        x: COL.reviewer,
        y,
        subtask: s,
        spans: [review],
      });
      edges.push({ id: `${execId}-${reviewId}`, from: execId, to: reviewId, tone: "done" });
      prevRightId = reviewId;
    } else if (tone === "active" || tone === "pending") {
      const reviewId = `review-${s.id}`;
      nodes.push({
        id: reviewId,
        kind: "review",
        lane: `review #${s.position}`,
        title: "Reviewer",
        role: "reviewer",
        statusLabel: "queued",
        tone: "pending",
        x: COL.reviewer,
        y,
        subtask: s,
        spans: [],
      });
      prevRightId = execId;
    } else {
      prevRightId = execId;
    }

    const subtaskEscalation = escalations.find((e) => e.subtask_id === s.id);
    if (subtaskEscalation) {
      const escId = `esc-${subtaskEscalation.id}`;
      nodes.push({
        id: escId,
        kind: "escalation",
        lane: "human-in-the-loop",
        title: "Approval gate",
        role: "human",
        statusLabel: subtaskEscalation.status === "pending" ? "awaiting decision" : subtaskEscalation.status,
        tone: subtaskEscalation.status === "pending" ? "esc" : "done",
        x: COL.human,
        y,
        escalation: subtaskEscalation,
        spans: [],
      });
      edges.push({ id: `${prevRightId}-${escId}`, from: prevRightId, to: escId, tone: "esc" });
    }

    lastRow = i;
  });

  const planEscalation = escalations.find((e) => !e.subtask_id);
  const synthesizeSpan = spans.find((s) => s.span_type === "synthesize");
  const synthRow = subtasks.length > 0 ? lastRow + 1 : 0;
  const synthY = ROW0 + synthRow * ROW_H;

  if (planEscalation) {
    const escId = `esc-${planEscalation.id}`;
    nodes.push({
      id: escId,
      kind: "escalation",
      lane: "human-in-the-loop · plan level",
      title: "Approval gate",
      role: "human",
      statusLabel: planEscalation.status === "pending" ? "awaiting decision" : planEscalation.status,
      tone: planEscalation.status === "pending" ? "esc" : "done",
      x: COL.human,
      y: synthY,
      escalation: planEscalation,
      spans: [],
    });
    edges.push({ id: `${prevRightId}-${escId}`, from: prevRightId, to: escId, tone: "esc", dashed: !subtasks.length });
    prevRightId = escId;
  }

  nodes.push({
    id: "synthesize",
    kind: "synthesize",
    lane: "synthesize",
    title: "Final answer",
    role: "supervisor",
    statusLabel: synthesizeSpan ? "✓ synthesized" : task.status === "failed" ? "not reached" : "not reached",
    tone: synthesizeSpan ? "done" : "pending",
    x: COL.synthesize,
    y: synthY,
    spans: synthesizeSpan ? [synthesizeSpan] : [],
  });
  edges.push({ id: `${prevRightId}-synthesize`, from: prevRightId, to: "synthesize", tone: synthesizeSpan ? "done" : "pending", dashed: !synthesizeSpan });

  return { nodes, edges };
}

function durationLabel(span: TraceSpanOut): string {
  if (!span.ended_at) return "running";
  const ms = new Date(span.ended_at).getTime() - new Date(span.started_at).getTime();
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`;
}
