import type {
  EscalationOut,
  TaskArtifact,
  TaskDetailOut,
  TaskMessageOut,
  TraceSpanOut,
} from "@/lib/api";

export type ExecutionActor =
  | "user"
  | "llm"
  | "runtime"
  | "tool"
  | "memory"
  | "knowledge"
  | "human";

export type ExecutionNodeType =
  | "goal"
  | "plan"
  | "llm"
  | "router"
  | "tool"
  | "subagent"
  | "memory"
  | "knowledge"
  | "policy"
  | "approval"
  | "execution"
  | "verification"
  | "recovery"
  | "synthesis"
  | "final";

export type ExecutionFamily =
  | "reasoning"
  | "action"
  | "context"
  | "control"
  | "quality"
  | "system";

export type ExecutionStatus =
  | "pending"
  | "running"
  | "waiting"
  | "approval_required"
  | "succeeded"
  | "failed"
  | "retrying"
  | "blocked"
  | "skipped"
  | "recovered"
  | "cancelled";

export type ExecutionEdgeRelation =
  | "sequence"
  | "parallel"
  | "route"
  | "uses_context"
  | "requires_approval"
  | "verifies"
  | "recovers"
  | "retries";

export interface ExecutionNode {
  id: string;
  runId: string;
  type: ExecutionNodeType;
  family: ExecutionFamily;
  actor: ExecutionActor;
  label: string;
  subtitle?: string;
  status: ExecutionStatus;
  startedAt?: string;
  completedAt?: string;
  parentId?: string;
  groupId?: string;
  traceSpanIds: string[];
  subtaskId?: string | null;
  toolName?: string;
  escalationId?: string;
  position: { x: number; y: number };
  metadata: Record<string, unknown>;
}

export interface ExecutionEdge {
  id: string;
  source: string;
  target: string;
  relation: ExecutionEdgeRelation;
  status?: ExecutionStatus;
}

export type RunEventKind =
  | "RUN_STARTED"
  | "RUN_STATUS_CHANGED"
  | "RUN_COMPLETED"
  | "NODE_STARTED"
  | "NODE_COMPLETED"
  | "NODE_FAILED"
  | "APPROVAL_REQUIRED"
  | "APPROVAL_RESOLVED"
  | "ARTIFACT_CREATED"
  | "STREAM_SNAPSHOT"
  | "STREAM_ERROR";

export interface RunEvent {
  id: string;
  kind: RunEventKind;
  runId: string;
  nodeId?: string;
  timestamp: string;
  label: string;
  actor: ExecutionActor;
  status?: ExecutionStatus;
  detail?: string;
  span?: TraceSpanOut;
  escalation?: EscalationOut;
  artifact?: TaskArtifact;
  message?: TaskMessageOut;
}

export type RuntimeCardKind =
  | "user"
  | "assistant"
  | "plan"
  | "approval"
  | "tool"
  | "verification"
  | "memory"
  | "artifact"
  | "error"
  | "system";

export interface RuntimeCard {
  id: string;
  kind: RuntimeCardKind;
  timestamp: string;
  title: string;
  body: string;
  nodeId?: string;
  status?: ExecutionStatus;
  escalation?: EscalationOut;
}

export interface ContextMetric {
  label: string;
  tokens: number | null;
  status: "included" | "reserved" | "dropped" | "unknown";
  reason?: string;
}

export interface ContextMetrics {
  windowTokens: number | null;
  reservedOutputTokens: number | null;
  usableInputTokens: number | null;
  selectedTokens: number | null;
  tokensAvoided: number | null;
  compressionRatio: number | null;
  rows: ContextMetric[];
  source: "derived" | "unavailable";
}

export interface RunModel {
  task: TaskDetailOut;
  nodes: ExecutionNode[];
  edges: ExecutionEdge[];
  events: RunEvent[];
  cards: RuntimeCard[];
  context: ContextMetrics;
  currentNodeId: string | null;
  pendingApproval: EscalationOut | null;
}
