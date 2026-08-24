import type { EscalationOut, SubtaskOut, TaskDetailOut, TaskMessageOut, TraceSpanOut } from "./api";
import type { AgentRole } from "./agentRoles";

export type GraphNodeKind = "sketch" | "execute" | "review" | "escalation" | "synthesize" | "message";
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
  message?: TaskMessageOut;
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

function durationLabel(span: TraceSpanOut): string {
  if (!span.ended_at) return "running";
  const ms = new Date(span.ended_at).getTime() - new Date(span.started_at).getTime();
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`;
}

export function buildGraph(
  task: TaskDetailOut,
  spans: TraceSpanOut[],
  escalations: EscalationOut[],
  messages: TaskMessageOut[] = []
): { nodes: GraphNodeData[]; edges: GraphEdgeData[] } {
  const nodes: GraphNodeData[] = [];
  const edges: GraphEdgeData[] = [];
  const subtasks = [...task.subtasks].sort((a, b) => a.position - b.position);
  const orderedMessages = [...messages].sort(
    (a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime()
  );

  const spansBySubtask = new Map<string, TraceSpanOut[]>();
  for (const s of spans) {
    if (!s.subtask_id) continue;
    const list = spansBySubtask.get(s.subtask_id) ?? [];
    list.push(s);
    spansBySubtask.set(s.subtask_id, list);
  }

  // Turn boundaries: turn 0 starts when the task was created, turn N starts
  // at the Nth follow-up message -- mirrors the backend's _turn_start
  // (graph/nodes.py), so a subtask created after message[i] but before
  // message[i+1] belongs to turn i+1. This is what lets a continued
  // conversation render as its own message -> subtasks -> synthesize
  // segment instead of silently merging into turn 0 or replacing it.
  const turnStarts = [
    new Date(task.created_at).getTime(),
    ...orderedMessages.map((m) => new Date(m.created_at).getTime()),
  ];
  function turnIndexFor(iso: string): number {
    const t = new Date(iso).getTime();
    let idx = 0;
    for (let i = 0; i < turnStarts.length; i++) if (t >= turnStarts[i]) idx = i;
    return idx;
  }

  const subtasksByTurn = new Map<number, SubtaskOut[]>();
  for (const s of subtasks) {
    const t = turnIndexFor(s.created_at);
    const list = subtasksByTurn.get(t) ?? [];
    list.push(s);
    subtasksByTurn.set(t, list);
  }

  const synthesizeSpans = [...spans]
    .filter((s) => s.span_type === "synthesize")
    .sort((a, b) => new Date(a.started_at).getTime() - new Date(b.started_at).getTime());
  const synthByTurn = new Map<number, TraceSpanOut>();
  for (const s of synthesizeSpans) synthByTurn.set(turnIndexFor(s.started_at), s);

  const planEscalationsByTurn = new Map<number, EscalationOut>();
  for (const e of escalations) {
    if (e.subtask_id) continue;
    planEscalationsByTurn.set(turnIndexFor(e.created_at), e);
  }

  // Turn 0's sketch node -- sketch only ever runs once, for the original request.
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
  let row = 0;
  const totalTurns = turnStarts.length;

  for (let turn = 0; turn < totalTurns; turn++) {
    if (turn > 0) {
      // The follow-up that started this turn -- rendered in the Supervisor
      // column since it's what the agent_step loop reacts to next.
      const message = orderedMessages[turn - 1];
      const msgId = `message-${message.id}`;
      const y = ROW0 + row * ROW_H;
      nodes.push({
        id: msgId,
        kind: "message",
        lane: "human · follow-up",
        title: "You",
        subtitle: message.content.length > 48 ? message.content.slice(0, 48) + "…" : message.content,
        role: "human",
        statusLabel: "sent",
        tone: "done",
        x: COL.supervisor,
        y,
        message,
        spans: [],
      });
      edges.push({ id: `${prevRightId}-${msgId}`, from: prevRightId, to: msgId, tone: "done" });
      prevRightId = msgId;
      row += 1;
    }

    const turnSubtasks = (subtasksByTurn.get(turn) ?? []).sort((a, b) => a.position - b.position);

    for (const s of turnSubtasks) {
      const y = ROW0 + row * ROW_H;
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

      row += 1;
    }

    const turnPlanEscalation = planEscalationsByTurn.get(turn);
    if (turnPlanEscalation) {
      const y = ROW0 + row * ROW_H;
      const escId = `esc-${turnPlanEscalation.id}`;
      nodes.push({
        id: escId,
        kind: "escalation",
        lane: "human-in-the-loop · plan level",
        title: "Approval gate",
        role: "human",
        statusLabel: turnPlanEscalation.status === "pending" ? "awaiting decision" : turnPlanEscalation.status,
        tone: turnPlanEscalation.status === "pending" ? "esc" : "done",
        x: COL.human,
        y,
        escalation: turnPlanEscalation,
        spans: [],
      });
      edges.push({ id: `${prevRightId}-${escId}`, from: prevRightId, to: escId, tone: "esc", dashed: turnSubtasks.length === 0 });
      prevRightId = escId;
      row += 1;
    }

    const turnSynth = synthByTurn.get(turn);
    const isLastTurn = turn === totalTurns - 1;
    // Only render a synthesize node for this turn if it actually produced
    // one, or this is the current (last) turn -- a turn that instead ended
    // in a plan-level escalation or failure shouldn't get a fake
    // "not reached" node sitting between two completed turns.
    if (turnSynth || isLastTurn) {
      const y = ROW0 + row * ROW_H;
      const synthId = totalTurns > 1 ? `synthesize-turn-${turn}` : "synthesize";
      nodes.push({
        id: synthId,
        kind: "synthesize",
        lane: totalTurns > 1 ? `synthesize · turn ${turn + 1}` : "synthesize",
        title: "Final answer",
        role: "supervisor",
        statusLabel: turnSynth ? "✓ synthesized" : "not reached",
        tone: turnSynth ? "done" : "pending",
        x: COL.synthesize,
        y,
        spans: turnSynth ? [turnSynth] : [],
      });
      edges.push({
        id: `${prevRightId}-${synthId}`,
        from: prevRightId,
        to: synthId,
        tone: turnSynth ? "done" : "pending",
        dashed: !turnSynth,
      });
      prevRightId = synthId;
      row += 1;
    }
  }

  return { nodes, edges };
}
