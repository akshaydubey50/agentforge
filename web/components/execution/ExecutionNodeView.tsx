import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import type { ComponentType } from "react";
import {
  Brain,
  CheckCircle2,
  CircleDashed,
  Clock3,
  Database,
  FileSearch,
  OctagonAlert,
  Play,
  RotateCcw,
  ShieldCheck,
  UserCheck,
  Wrench,
  XCircle,
} from "lucide-react";
import type { ExecutionFamily, ExecutionNode, ExecutionStatus } from "@/lib/execution/types";
import { cn } from "@/lib/utils";

export type ExecutionNodeData = ExecutionNode & Record<string, unknown>;
export type ExecutionFlowNode = Node<ExecutionNodeData, "executionNode">;

const FAMILY_META: Record<ExecutionFamily, { label: string; icon: ComponentType<{ className?: string }>; className: string }> = {
  reasoning: { label: "LLM", icon: Brain, className: "border-role-supervisor/65 bg-role-supervisor/8 text-role-supervisor" },
  action: { label: "Tool", icon: Wrench, className: "border-status-running/65 bg-status-running/8 text-status-running" },
  context: { label: "Context", icon: Database, className: "border-ok/60 bg-ok/8 text-ok" },
  control: { label: "Runtime", icon: ShieldCheck, className: "border-role-human/70 bg-role-human/8 text-role-human" },
  quality: { label: "Verify", icon: FileSearch, className: "border-status-completed/65 bg-status-completed/8 text-status-completed" },
  system: { label: "System", icon: CircleDashed, className: "border-border-strong bg-surface-2 text-text-muted" },
};

const STATUS_META: Record<ExecutionStatus, { label: string; icon: ComponentType<{ className?: string }>; className: string }> = {
  pending: { label: "pending", icon: CircleDashed, className: "text-text-faint" },
  running: { label: "running", icon: Play, className: "text-status-running" },
  waiting: { label: "waiting", icon: Clock3, className: "text-status-pending" },
  approval_required: { label: "approval", icon: UserCheck, className: "text-role-human" },
  succeeded: { label: "succeeded", icon: CheckCircle2, className: "text-status-completed" },
  failed: { label: "failed", icon: XCircle, className: "text-status-failed" },
  retrying: { label: "retrying", icon: RotateCcw, className: "text-status-revision" },
  blocked: { label: "blocked", icon: OctagonAlert, className: "text-status-failed" },
  skipped: { label: "skipped", icon: CircleDashed, className: "text-text-faint" },
  recovered: { label: "recovered", icon: RotateCcw, className: "text-status-completed" },
  cancelled: { label: "cancelled", icon: XCircle, className: "text-text-faint" },
};

export function statusLabel(status: ExecutionStatus) {
  return STATUS_META[status].label;
}

export function actorLabel(node: ExecutionNode) {
  if (node.actor === "llm") return "Model output";
  if (node.actor === "runtime") return "AgentForge runtime";
  if (node.actor === "tool") return "External tool";
  if (node.actor === "human") return "Human";
  if (node.actor === "memory") return "Memory";
  if (node.actor === "knowledge") return "Knowledge";
  return "User";
}

export function ExecutionNodeView({ data, selected }: NodeProps<ExecutionFlowNode>) {
  const family = FAMILY_META[data.family];
  const status = STATUS_META[data.status];
  const FamilyIcon = family.icon;
  const StatusIcon = status.icon;
  const active = data.status === "running" || data.status === "approval_required" || data.status === "retrying";

  return (
    <button
      type="button"
      className={cn(
        "group w-[218px] rounded-[8px] border bg-surface px-3.5 py-3 text-left shadow-sm transition",
        family.className,
        active && "shadow-[0_0_24px_rgba(245,166,35,0.13)]",
        selected && "ring-2 ring-role-supervisor/80"
      )}
      aria-label={`${data.label}, ${family.label}, ${status.label}`}
    >
      <Handle type="target" position={Position.Left} className="!border-0 !bg-border-strong" />
      <Handle type="source" position={Position.Right} className="!border-0 !bg-border-strong" />
      <div className="mb-2 flex items-center justify-between gap-2">
        <span className="inline-flex items-center gap-1.5 text-[10px] uppercase tracking-[0.1em] text-current">
          <FamilyIcon className="h-3.5 w-3.5" />
          {family.label}
        </span>
        <span className={cn("inline-flex items-center gap-1 rounded-full bg-background/55 px-1.5 py-0.5 text-[10px]", status.className)}>
          <StatusIcon className={cn("h-3 w-3", active && "motion-safe:animate-pulse")} />
          {status.label}
        </span>
      </div>
      <div className="line-clamp-2 text-[13px] font-semibold leading-snug text-text">{data.label}</div>
      {data.subtitle && <div className="mt-1 truncate font-mono text-[11px] text-text-muted">{data.subtitle}</div>}
      <div className="mt-2 truncate text-[10.5px] text-text-faint">{actorLabel(data)}</div>
    </button>
  );
}
