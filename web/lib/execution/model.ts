import type {
  EscalationOut,
  SubtaskOut,
  TaskArtifact,
  TaskDetailOut,
  TaskMessageOut,
  TraceSpanOut,
} from "@/lib/api";
import type { TaskEvent } from "@/lib/useTaskEvents";
import type {
  ContextMetrics,
  ExecutionEdge,
  ExecutionActor,
  ExecutionFamily,
  ExecutionNode,
  ExecutionNodeType,
  ExecutionStatus,
  RunEvent,
  RunModel,
  RuntimeCard,
} from "./types";

type NodeDraft = Omit<ExecutionNode, "position">;
type SemanticGroupId =
  | "plan"
  | "memory"
  | "knowledge"
  | "model-work"
  | "tool-work"
  | "subagents"
  | "verification"
  | "synthesis"
  | "recovery";

interface SemanticGroup {
  id: SemanticGroupId;
  type: ExecutionNodeType;
  actor: ExecutionActor;
  label: string;
  subtitle: string;
}

const FAMILY_FOR_TYPE: Record<ExecutionNodeType, ExecutionFamily> = {
  goal: "system",
  plan: "reasoning",
  llm: "reasoning",
  router: "system",
  tool: "action",
  subagent: "action",
  memory: "context",
  knowledge: "context",
  policy: "control",
  approval: "control",
  execution: "action",
  verification: "quality",
  recovery: "system",
  synthesis: "reasoning",
  final: "system",
};

const COLUMN_X: Record<ExecutionNodeType, number> = {
  goal: 40,
  plan: 300,
  llm: 300,
  router: 300,
  memory: 560,
  knowledge: 560,
  tool: 820,
  subagent: 820,
  execution: 820,
  policy: 1080,
  approval: 1080,
  verification: 1340,
  recovery: 1340,
  synthesis: 1340,
  final: 1600,
};

const SEMANTIC_GROUPS: Record<SemanticGroupId, SemanticGroup> = {
  plan: {
    id: "plan",
    type: "plan",
    actor: "llm",
    label: "Plan",
    subtitle: "approach and step outline",
  },
  memory: {
    id: "memory",
    type: "memory",
    actor: "memory",
    label: "Memory",
    subtitle: "retrieved or updated context",
  },
  knowledge: {
    id: "knowledge",
    type: "knowledge",
    actor: "knowledge",
    label: "Knowledge",
    subtitle: "retrieved evidence and documents",
  },
  "model-work": {
    id: "model-work",
    type: "llm",
    actor: "llm",
    label: "Model work",
    subtitle: "reasoning, routing, and tool selection",
  },
  "tool-work": {
    id: "tool-work",
    type: "tool",
    actor: "tool",
    label: "Tool work",
    subtitle: "validated calls and results",
  },
  subagents: {
    id: "subagents",
    type: "subagent",
    actor: "tool",
    label: "Subagents",
    subtitle: "delegated execution",
  },
  verification: {
    id: "verification",
    type: "verification",
    actor: "llm",
    label: "Verification",
    subtitle: "expected vs observed checks",
  },
  synthesis: {
    id: "synthesis",
    type: "synthesis",
    actor: "llm",
    label: "Synthesis",
    subtitle: "final response assembly",
  },
  recovery: {
    id: "recovery",
    type: "recovery",
    actor: "runtime",
    label: "Recovery",
    subtitle: "retry or ambiguous-effect handling",
  },
};

const SEMANTIC_GROUP_ORDER: SemanticGroupId[] = [
  "plan",
  "memory",
  "knowledge",
  "model-work",
  "tool-work",
  "subagents",
  "verification",
  "synthesis",
  "recovery",
];

function familyFor(type: ExecutionNodeType): ExecutionFamily {
  return FAMILY_FOR_TYPE[type];
}

function durationMs(start?: string, end?: string | null): number | null {
  if (!start || !end) return null;
  return Math.max(0, new Date(end).getTime() - new Date(start).getTime());
}

function durationLabel(start?: string, end?: string | null): string | undefined {
  const ms = durationMs(start, end);
  if (ms === null) return undefined;
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`;
}

function taskStatus(status: TaskDetailOut["status"]): ExecutionStatus {
  switch (status) {
    case "pending":
      return "pending";
    case "running":
      return "running";
    case "awaiting_approval":
      return "approval_required";
    case "completed":
      return "succeeded";
    case "failed":
      return "failed";
    case "cancelled":
      return "cancelled";
    default:
      return "pending";
  }
}

function subtaskStatus(status: SubtaskOut["status"], attempts: number): ExecutionStatus {
  switch (status) {
    case "done":
      return attempts > 1 ? "recovered" : "succeeded";
    case "running":
      return "running";
    case "escalated":
      return "approval_required";
    case "failed":
      return "failed";
    case "skipped":
      return "skipped";
    case "needs_revision":
      return "retrying";
    case "ready":
      return "waiting";
    default:
      return "pending";
  }
}

function spanStatus(span: TraceSpanOut): ExecutionStatus {
  if (!span.ended_at) return "running";
  return span.status === "ok" ? "succeeded" : "failed";
}

function actorForSpan(span: TraceSpanOut): ExecutionActor {
  if (span.span_type === "tool_call") return "tool";
  if (span.span_type === "memory") return "memory";
  if (span.span_type === "escalation") return "runtime";
  if (span.span_type === "review") return "llm";
  if (span.span_type === "agent_step") return "runtime";
  return "llm";
}

function typeForSpan(span: TraceSpanOut): ExecutionNodeType {
  if (span.span_type === "sketch") return "plan";
  if (span.span_type === "tool_selection") return "llm";
  if (span.span_type === "tool_call") {
    if (span.name.includes("knowledge")) return "knowledge";
    if (span.name.includes("memory")) return "memory";
    if (span.name.includes("delegate")) return "subagent";
    return "tool";
  }
  if (span.span_type === "memory") return "memory";
  if (span.span_type === "review") return "verification";
  if (span.span_type === "synthesize") return "synthesis";
  if (span.span_type === "agent_step") return "router";
  if (span.name.toLowerCase().includes("verification")) return "verification";
  if (span.name.toLowerCase().includes("recovery")) return "recovery";
  return "llm";
}

function labelForSpan(span: TraceSpanOut): string {
  if (span.span_type === "sketch") return "Plan";
  if (span.span_type === "agent_step") return "Runtime step";
  if (span.span_type === "tool_selection") return "Tool proposal";
  if (span.span_type === "reasoning") return "Model output";
  if (span.span_type === "review") return "Verification";
  if (span.span_type === "synthesize") return "Synthesis";
  if (span.span_type === "memory") return "Memory";
  return span.name || span.span_type;
}

function shortText(value: unknown, fallback = "No summary available."): string {
  if (typeof value === "string" && value.trim()) return value.trim();
  if (Array.isArray(value)) return value.map((v) => shortText(v, "")).filter(Boolean).join("; ") || fallback;
  if (value && typeof value === "object") {
    const record = value as Record<string, unknown>;
    for (const key of ["summary", "text", "final_answer", "reason", "rationale", "error"]) {
      const v = record[key];
      if (typeof v === "string" && v.trim()) return v.trim();
    }
    const keys = Object.keys(record).slice(0, 4);
    if (keys.length) return keys.map((k) => `${k}: ${shortText(record[k], "")}`).join("; ");
  }
  return fallback;
}

function toolNameFromEscalation(escalation: EscalationOut): string | undefined {
  const tool = escalation.context?.tool_name;
  return typeof tool === "string" ? tool : undefined;
}

function policyFromEscalation(escalation: EscalationOut): Record<string, unknown> | null {
  const policy = escalation.context?.policy;
  return policy && typeof policy === "object" ? (policy as Record<string, unknown>) : null;
}

function escalationStatus(escalation: EscalationOut): ExecutionStatus {
  if (escalation.status === "pending") return "approval_required";
  if (escalation.status === "approved") return "succeeded";
  if (escalation.status === "rejected") return "failed";
  return "recovered";
}

function sortedSpans(spans: TraceSpanOut[]): TraceSpanOut[] {
  return [...spans].sort((a, b) => new Date(a.started_at).getTime() - new Date(b.started_at).getTime());
}

function sortedEscalations(escalations: EscalationOut[]): EscalationOut[] {
  return [...escalations].sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime());
}

function addNode(nodes: NodeDraft[], node: NodeDraft): void {
  if (!nodes.some((n) => n.id === node.id)) nodes.push(node);
}

function makeNode(args: {
  id: string;
  runId: string;
  type: ExecutionNodeType;
  actor: ExecutionActor;
  label: string;
  subtitle?: string;
  status: ExecutionStatus;
  startedAt?: string;
  completedAt?: string;
  traceSpanIds?: string[];
  subtaskId?: string | null;
  toolName?: string;
  escalationId?: string;
  metadata?: Record<string, unknown>;
}): NodeDraft {
  return {
    id: args.id,
    runId: args.runId,
    type: args.type,
    family: familyFor(args.type),
    actor: args.actor,
    label: args.label,
    subtitle: args.subtitle,
    status: args.status,
    startedAt: args.startedAt,
    completedAt: args.completedAt,
    traceSpanIds: args.traceSpanIds ?? [],
    subtaskId: args.subtaskId,
    toolName: args.toolName,
    escalationId: args.escalationId,
    metadata: args.metadata ?? {},
  };
}

function latestDefined<T>(items: T[], pick: (item: T) => unknown): unknown {
  for (let i = items.length - 1; i >= 0; i--) {
    const value = pick(items[i]);
    if (value !== undefined && value !== null && value !== "") return value;
  }
  return undefined;
}

function spanGroupId(span: TraceSpanOut): SemanticGroupId {
  const type = typeForSpan(span);
  if (type === "plan") return "plan";
  if (type === "memory") return "memory";
  if (type === "knowledge") return "knowledge";
  if (type === "tool" || type === "execution") return "tool-work";
  if (type === "subagent") return "subagents";
  if (type === "verification") return "verification";
  if (type === "synthesis") return "synthesis";
  if (type === "recovery") return "recovery";
  return "model-work";
}

function liveGroupId(event: TaskEvent): SemanticGroupId {
  if (event.span_type === "tool_call") {
    const name = event.name ?? "";
    if (name.includes("knowledge")) return "knowledge";
    if (name.includes("memory")) return "memory";
    if (name.includes("delegate")) return "subagents";
    return "tool-work";
  }
  if (event.span_type === "memory") return "memory";
  if (event.span_type === "review") return "verification";
  if (event.span_type === "synthesize") return "synthesis";
  if (event.span_type === "sketch") return "plan";
  return "model-work";
}

function nodeIdForSpan(span: TraceSpanOut): string {
  return spanGroupId(span);
}

function aggregateStatuses(statuses: ExecutionStatus[], fallback: ExecutionStatus): ExecutionStatus {
  if (statuses.length === 0) return fallback;
  if (statuses.includes("approval_required")) return "approval_required";
  if (statuses.includes("running")) return "running";
  if (statuses.includes("failed")) return "failed";
  if (statuses.includes("retrying")) return "retrying";
  if (statuses.every((status) => status === "skipped")) return "skipped";
  if (statuses.some((status) => status === "pending" || status === "waiting")) return "waiting";
  if (statuses.includes("recovered")) return "recovered";
  if (statuses.every((status) => status === "succeeded" || status === "recovered")) return "succeeded";
  return statuses.at(-1) ?? fallback;
}

function formatCount(count: number, singular: string, plural = `${singular}s`): string {
  return `${count} ${count === 1 ? singular : plural}`;
}

function spanSummary(span: TraceSpanOut): Record<string, unknown> {
  return {
    id: span.id,
    type: span.span_type,
    name: span.name,
    status: spanStatus(span),
    started_at: span.started_at,
    ended_at: span.ended_at,
    duration: durationLabel(span.started_at, span.ended_at),
    detail: shortText(span.output, span.name),
  };
}

function buildSemanticNode(args: {
  runId: string;
  groupId: SemanticGroupId;
  spans: TraceSpanOut[];
  subtasks?: SubtaskOut[];
  liveEvents?: TaskEvent[];
}): NodeDraft {
  const group = SEMANTIC_GROUPS[args.groupId];
  const spans = sortedSpans(args.spans);
  const subtasks = args.subtasks ?? [];
  const liveEvents = args.liveEvents ?? [];
  const statuses = [
    ...spans.map(spanStatus),
    ...subtasks.map((subtask) => subtaskStatus(subtask.status, subtask.attempt_count)),
    ...liveEvents.map(() => "running" as ExecutionStatus),
  ];
  const startedAt =
    spans[0]?.started_at ??
    subtasks[0]?.created_at ??
    liveEvents[0]?.ts;
  const completedAt = spans.length > 0 && spans.every((span) => span.ended_at) ? spans.at(-1)?.ended_at ?? undefined : undefined;
  const toolNames = Array.from(
    new Set([
      ...spans.filter((span) => span.span_type === "tool_call").map((span) => span.name),
      ...subtasks.map((subtask) => subtask.assigned_tool).filter((tool): tool is string => Boolean(tool)),
    ])
  );
  const subtaskIds = Array.from(
    new Set([
      ...spans.map((span) => span.subtask_id).filter((id): id is string => Boolean(id)),
      ...subtasks.map((subtask) => subtask.id),
      ...liveEvents.map((event) => event.subtask_id).filter((id): id is string => Boolean(id)),
    ])
  );
  const workItems = Math.max(spans.length + subtasks.length + liveEvents.length, 1);
  const output = latestDefined(spans, (span) => span.output) ?? latestDefined(subtasks, (subtask) => subtask.output);
  const input = latestDefined(spans, (span) => span.input) ?? latestDefined(subtasks, (subtask) => subtask.description);

  return makeNode({
    id: group.id,
    runId: args.runId,
    type: group.type,
    actor: group.actor,
    label: group.label,
    subtitle: `${formatCount(workItems, "event")} - ${group.subtitle}`,
    status: aggregateStatuses(statuses, "waiting"),
    startedAt,
    completedAt: completedAt ?? undefined,
    traceSpanIds: spans.map((span) => span.id),
    subtaskId: subtaskIds.length === 1 ? subtaskIds[0] : undefined,
    toolName: toolNames.length === 1 ? toolNames[0] : undefined,
    metadata: {
      input,
      output,
      duration: startedAt && completedAt ? durationLabel(startedAt, completedAt) : undefined,
      spanCount: spans.length,
      subtaskCount: subtasks.length,
      liveEventCount: liveEvents.length,
      toolNames,
      subtaskIds,
      spans: spans.map(spanSummary),
      subtasks,
      liveEvents,
    },
  });
}

function withPositions(nodes: NodeDraft[]): ExecutionNode[] {
  const rowByColumn: Partial<Record<ExecutionNodeType, number>> = {};
  return nodes.map((node) => {
    const row = rowByColumn[node.type] ?? 0;
    rowByColumn[node.type] = row + 1;
    return {
      ...node,
      position: {
        x: COLUMN_X[node.type],
        y: 70 + row * 132,
      },
    };
  });
}

function nodeIdForEscalation(escalation: EscalationOut): string {
  return `approval-${escalation.id}`;
}

function buildNodes(task: TaskDetailOut, spans: TraceSpanOut[], escalations: EscalationOut[], events: TaskEvent[]): NodeDraft[] {
  const nodes: NodeDraft[] = [];
  const runId = task.id;
  const orderedSpans = sortedSpans(spans);
  const spansByGroup = new Map<SemanticGroupId, TraceSpanOut[]>();
  const subtasksByGroup = new Map<SemanticGroupId, SubtaskOut[]>();
  const liveEventsByGroup = new Map<SemanticGroupId, TaskEvent[]>();

  addNode(
    nodes,
    makeNode({
      id: "goal",
      runId,
      type: "goal",
      actor: "user",
      label: "Goal",
      subtitle: task.request_text,
      status: orderedSpans.length || task.status !== "pending" ? "succeeded" : taskStatus(task.status),
      startedAt: task.created_at,
      completedAt: orderedSpans[0]?.started_at,
      metadata: { request: task.request_text },
    })
  );

  for (const span of orderedSpans) {
    const groupId = spanGroupId(span);
    spansByGroup.set(groupId, [...(spansByGroup.get(groupId) ?? []), span]);
  }

  for (const subtask of task.subtasks) {
    const hasSpan = orderedSpans.some((span) => span.subtask_id === subtask.id);
    if (hasSpan) continue;
    const groupId: SemanticGroupId = subtask.assigned_tool ? "tool-work" : "model-work";
    subtasksByGroup.set(groupId, [...(subtasksByGroup.get(groupId) ?? []), subtask]);
  }

  for (const event of events) {
    if (event.kind !== "span_start" || !event.span_id || orderedSpans.some((span) => span.id === event.span_id)) {
      continue;
    }
    const groupId = liveGroupId(event);
    liveEventsByGroup.set(groupId, [...(liveEventsByGroup.get(groupId) ?? []), event]);
  }

  for (const groupId of SEMANTIC_GROUP_ORDER) {
    const groupSpans = spansByGroup.get(groupId) ?? [];
    const groupSubtasks = subtasksByGroup.get(groupId) ?? [];
    const groupLiveEvents = liveEventsByGroup.get(groupId) ?? [];
    if (groupSpans.length === 0 && groupSubtasks.length === 0 && groupLiveEvents.length === 0) continue;
    addNode(
      nodes,
      buildSemanticNode({
        runId,
        groupId,
        spans: groupSpans,
        subtasks: groupSubtasks,
        liveEvents: groupLiveEvents,
      })
    );
  }

  for (const escalation of sortedEscalations(escalations)) {
    const policy = policyFromEscalation(escalation);
    if (policy) {
      addNode(
        nodes,
        makeNode({
          id: `policy-${escalation.id}`,
          runId,
          type: "policy",
          actor: "runtime",
          label: "Policy decision",
          subtitle: String(policy.decision ?? "requires decision"),
          status: escalation.status === "pending" ? "approval_required" : "succeeded",
          startedAt: escalation.created_at,
          subtaskId: escalation.subtask_id,
          toolName: toolNameFromEscalation(escalation),
          escalationId: escalation.id,
          metadata: { policy, escalation },
        })
      );
    }
    addNode(
      nodes,
      makeNode({
        id: nodeIdForEscalation(escalation),
        runId,
        type: "approval",
        actor: "human",
        label: escalation.kind === "tool_approval" ? "Approval required" : "Human decision",
        subtitle: toolNameFromEscalation(escalation) ?? escalation.kind,
        status: escalationStatus(escalation),
        startedAt: escalation.created_at,
        completedAt: escalation.decided_at ?? undefined,
        subtaskId: escalation.subtask_id,
        toolName: toolNameFromEscalation(escalation),
        escalationId: escalation.id,
        metadata: { escalation },
      })
    );
  }

  const terminal = task.status === "completed" || task.status === "failed" || task.status === "cancelled";
  if (terminal) {
    addNode(
      nodes,
      makeNode({
        id: "final",
        runId,
        type: "final",
        actor: "runtime",
        label: task.status === "completed" ? "Complete" : task.status === "cancelled" ? "Cancelled" : "Failed",
        subtitle: task.final_output ?? undefined,
        status: taskStatus(task.status),
        startedAt: task.updated_at,
        completedAt: task.updated_at,
        metadata: { finalOutput: task.final_output },
      })
    );
  }

  return nodes;
}

function findNodeForEscalation(nodes: NodeDraft[], escalation: EscalationOut): string | undefined {
  if (escalation.subtask_id) {
    const related = nodes
      .filter((node) => {
        if (node.type === "approval" || node.type === "policy") return false;
        const subtaskIds = Array.isArray(node.metadata.subtaskIds) ? node.metadata.subtaskIds : [];
        return node.subtaskId === escalation.subtask_id || subtaskIds.includes(escalation.subtask_id);
      })
      .at(-1);
    if (related) return related.id;
  }
  return nodes.at(-1)?.id;
}

function buildEdges(nodes: NodeDraft[], escalations: EscalationOut[]): ExecutionEdge[] {
  const edges: ExecutionEdge[] = [];
  const nodeIds = new Set(nodes.map((node) => node.id));

  for (let i = 0; i < nodes.length - 1; i++) {
    const source = nodes[i].id;
    const target = nodes[i + 1].id;
    const relation = nodes[i].type === "policy" && nodes[i + 1].type === "approval" ? "requires_approval" : "sequence";
    edges.push({
      id: `${source}->${target}`,
      source,
      target,
      relation,
      status: nodes[i + 1].status,
    });
  }

  for (const escalation of escalations) {
    const policyId = `policy-${escalation.id}`;
    const approvalId = nodeIdForEscalation(escalation);
    if (nodeIds.has(policyId) && nodeIds.has(approvalId)) {
      edges.push({
        id: `${policyId}->${approvalId}`,
        source: policyId,
        target: approvalId,
        relation: "requires_approval" as const,
        status: escalationStatus(escalation),
      });
    } else if (nodeIds.has(approvalId)) {
      const source = findNodeForEscalation(nodes, escalation);
      if (source && source !== approvalId) {
        edges.push({
          id: `${source}->${approvalId}`,
          source,
          target: approvalId,
          relation: "requires_approval" as const,
          status: escalationStatus(escalation),
        });
      }
    }
  }

  const seen = new Set<string>();
  return edges.filter((edge) => {
    if (seen.has(edge.id)) return false;
    seen.add(edge.id);
    return true;
  });
}

function statusForRunEvent(kind: RunEvent["kind"], span?: TraceSpanOut, escalation?: EscalationOut): ExecutionStatus | undefined {
  if (span) return spanStatus(span);
  if (escalation) return escalationStatus(escalation);
  if (kind === "STREAM_ERROR") return "failed";
  return undefined;
}

function buildEvents(
  task: TaskDetailOut,
  nodes: ExecutionNode[],
  spans: TraceSpanOut[],
  escalations: EscalationOut[],
  artifacts: TaskArtifact[],
  liveEvents: TaskEvent[]
): RunEvent[] {
  const events: RunEvent[] = [
    {
      id: "run-started",
      kind: "RUN_STARTED",
      runId: task.id,
      nodeId: "goal",
      timestamp: task.created_at,
      label: "Goal accepted",
      actor: "runtime",
      status: "succeeded",
      detail: task.request_text,
    },
  ];

  for (const span of sortedSpans(spans)) {
    const nodeId = nodeIdForSpan(span);
    const eventStatus = statusForRunEvent("NODE_COMPLETED", span);
    events.push({
      id: `span-${span.id}`,
      kind: eventStatus === "running" ? "NODE_STARTED" : span.status === "ok" ? "NODE_COMPLETED" : "NODE_FAILED",
      runId: task.id,
      nodeId,
      timestamp: span.ended_at ?? span.started_at,
      label: labelForSpan(span),
      actor: actorForSpan(span),
      status: eventStatus,
      detail: shortText(span.output, span.name),
      span,
    });
  }

  for (const escalation of sortedEscalations(escalations)) {
    const pending = escalation.status === "pending";
    events.push({
      id: `escalation-${escalation.id}`,
      kind: pending ? "APPROVAL_REQUIRED" : "APPROVAL_RESOLVED",
      runId: task.id,
      nodeId: nodeIdForEscalation(escalation),
      timestamp: pending ? escalation.created_at : escalation.decided_at ?? escalation.created_at,
      label: pending ? "Approval required" : `Approval ${escalation.status}`,
      actor: "human",
      status: statusForRunEvent("APPROVAL_REQUIRED", undefined, escalation),
      detail: escalation.reason,
      escalation,
    });
  }

  for (const artifact of artifacts) {
    events.push({
      id: `artifact-${artifact.path}`,
      kind: "ARTIFACT_CREATED",
      runId: task.id,
      timestamp: artifact.modified_at,
      label: "Artifact created",
      actor: "runtime",
      status: "succeeded",
      detail: artifact.path,
      artifact,
    });
  }

  for (const event of liveEvents) {
    if (event.kind === "span_start" && event.span_id && !spans.some((span) => span.id === event.span_id)) {
      const nodeId = liveGroupId(event);
      events.push({
        id: `live-span-${event.span_id}`,
        kind: "NODE_STARTED",
        runId: task.id,
        nodeId,
        timestamp: event.ts,
        label: event.name ?? event.span_type ?? "Running",
        actor: SEMANTIC_GROUPS[nodeId].actor,
        status: "running",
        detail: "Live event received; durable trace detail will fill in after refetch.",
      });
    }
    if (event.kind === "snapshot") {
      events.push({
        id: `snapshot-${event.ts}`,
        kind: "STREAM_SNAPSHOT",
        runId: task.id,
        timestamp: event.ts,
        label: "Stream snapshot",
        actor: "runtime",
        detail: `Current backend status: ${event.status ?? "unknown"}`,
      });
    }
    if (event.kind === "stream_error") {
      events.push({
        id: `stream-error-${event.ts}`,
        kind: "STREAM_ERROR",
        runId: task.id,
        timestamp: event.ts,
        label: "Stream degraded",
        actor: "runtime",
        status: "failed",
        detail: event.detail ?? "Live narration failed; durable state remains available.",
      });
    }
  }

  if (task.status === "completed" || task.status === "failed" || task.status === "cancelled") {
    events.push({
      id: "run-terminal",
      kind: "RUN_COMPLETED",
      runId: task.id,
      nodeId: "final",
      timestamp: task.updated_at,
      label: task.status === "completed" ? "Run completed" : task.status === "cancelled" ? "Run cancelled" : "Run failed",
      actor: "runtime",
      status: taskStatus(task.status),
      detail: task.final_output ?? undefined,
    });
  }

  events.sort((a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime());
  return events.filter((event) => !event.nodeId || nodes.some((node) => node.id === event.nodeId) || event.nodeId === "goal");
}

function buildCards(task: TaskDetailOut, escalations: EscalationOut[]): RuntimeCard[] {
  const cards: RuntimeCard[] = [
    {
      id: "user-request",
      kind: "user",
      timestamp: task.created_at,
      title: "User goal",
      body: task.request_text,
      nodeId: "goal",
      status: "succeeded",
    },
  ];

  for (const message of task.messages) {
    cards.push({
      id: `message-${message.id}`,
      kind: "user",
      timestamp: message.created_at,
      title: "Follow-up",
      body: message.content,
      status: "succeeded",
    });
  }

  for (const escalation of sortedEscalations(escalations)) {
    cards.push({
      id: `approval-${escalation.id}`,
      kind: "approval",
      timestamp: escalation.created_at,
      title: escalation.status === "pending" ? "Approval required" : `Approval ${escalation.status}`,
      body: escalation.reason,
      nodeId: nodeIdForEscalation(escalation),
      status: escalationStatus(escalation),
      escalation,
    });
  }

  if (task.final_output) {
    cards.push({
      id: "final-answer",
      kind: "assistant",
      timestamp: task.updated_at,
      title: "Final answer",
      body: task.final_output,
      nodeId: "final",
      status: taskStatus(task.status),
    });
  }

  return cards.sort((a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime());
}

function buildContextMetrics(task: TaskDetailOut, spans: TraceSpanOut[]): ContextMetrics {
  const latestContext = [...spans].reverse().find((span) => {
    const output = span.output as Record<string, unknown>;
    return output && typeof output === "object" && ("context" in output || "context_metrics" in output);
  });
  const context = latestContext?.output?.context_metrics ?? latestContext?.output?.context;

  if (context && typeof context === "object") {
    const record = context as Record<string, unknown>;
    const numberValue = (key: string) => (typeof record[key] === "number" ? (record[key] as number) : null);
    return {
      windowTokens: numberValue("window_tokens"),
      reservedOutputTokens: numberValue("reserved_output_tokens"),
      usableInputTokens: numberValue("usable_input_tokens"),
      selectedTokens: numberValue("selected_tokens"),
      tokensAvoided: numberValue("tokens_avoided"),
      compressionRatio: numberValue("compression_ratio"),
      rows: [
        { label: "Conversation", tokens: numberValue("conversation_tokens"), status: "included" },
        { label: "Rolling summary", tokens: numberValue("summary_tokens"), status: task.rolling_summary ? "included" : "unknown" },
        { label: "Memory", tokens: numberValue("memory_tokens"), status: "included" },
        { label: "RAG", tokens: numberValue("rag_tokens"), status: "included" },
        { label: "Tool results", tokens: numberValue("tool_result_tokens"), status: "included" },
        { label: "Artifacts", tokens: numberValue("artifact_tokens"), status: "included" },
      ],
      source: "derived",
    };
  }

  return {
    windowTokens: null,
    reservedOutputTokens: null,
    usableInputTokens: null,
    selectedTokens: null,
    tokensAvoided: null,
    compressionRatio: null,
    rows: [
      {
        label: "Rolling summary",
        tokens: null,
        status: task.rolling_summary ? "included" : "unknown",
        reason: task.rolling_summary ? "Task has a persisted rolling summary." : "No rolling summary recorded.",
      },
      {
        label: "Memory",
        tokens: null,
        status: "unknown",
        reason: "Per-call memory token metrics are not exposed by the current API.",
      },
      {
        label: "RAG",
        tokens: null,
        status: "unknown",
        reason: "Retrieved chunk token metrics require a future enriched span shape.",
      },
      {
        label: "Tool results",
        tokens: null,
        status: "unknown",
        reason: "Tool result token contribution is not exposed per model call yet.",
      },
    ],
    source: "unavailable",
  };
}

export function buildRunModel(args: {
  task: TaskDetailOut;
  spans?: TraceSpanOut[];
  escalations?: EscalationOut[];
  artifacts?: TaskArtifact[];
  liveEvents?: TaskEvent[];
}): RunModel {
  const spans = args.spans ?? [];
  const escalations = args.escalations ?? [];
  const artifacts = args.artifacts ?? [];
  const liveEvents = args.liveEvents ?? [];
  const drafts = buildNodes(args.task, spans, escalations, liveEvents);
  const nodes = withPositions(drafts);
  const edges = buildEdges(drafts, escalations);
  const pendingApproval = sortedEscalations(escalations).find((e) => e.status === "pending") ?? null;
  const currentNodeId =
    nodes.find((node) => node.status === "approval_required")?.id ??
    nodes.find((node) => node.status === "running")?.id ??
    nodes.at(-1)?.id ??
    null;

  return {
    task: args.task,
    nodes,
    edges,
    events: buildEvents(args.task, nodes, spans, escalations, artifacts, liveEvents),
    cards: buildCards(args.task, escalations),
    context: buildContextMetrics(args.task, spans),
    currentNodeId,
    pendingApproval,
  };
}

export function runtimeCardNode(card: RuntimeCard): string | undefined {
  return card.nodeId;
}

export function eventNode(event: RunEvent): string | undefined {
  return event.nodeId;
}
