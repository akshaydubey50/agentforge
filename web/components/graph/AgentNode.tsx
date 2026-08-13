import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import { ROLE_META } from "@/lib/agentRoles";
import type { GraphNodeData, StatusTone } from "@/lib/agentGraph";
import { cn } from "@/lib/utils";

export type AgentFlowNode = Node<GraphNodeData, "agentNode">;

const TONE_BORDER: Record<StatusTone, string> = {
  done: "border-status-completed",
  active: "border-status-running shadow-[0_0_22px_rgba(245,166,35,0.22)]",
  esc: "border-role-human",
  failed: "border-status-failed",
  pending: "border-border opacity-55",
};

const TONE_BADGE: Record<StatusTone, string> = {
  done: "bg-status-completed/15 text-status-completed",
  active: "bg-status-running/15 text-status-running",
  esc: "bg-role-human/15 text-role-human",
  failed: "bg-status-failed/15 text-status-failed",
  pending: "bg-status-pending/15 text-text-faint",
};

export function AgentNode({ data, selected }: NodeProps<AgentFlowNode>) {
  const meta = ROLE_META[data.role];
  return (
    <div
      className={cn(
        "w-[188px] rounded-xl border bg-surface px-3.5 py-3 text-left transition-shadow",
        TONE_BORDER[data.tone],
        selected && "ring-2 ring-role-supervisor"
      )}
    >
      <Handle type="target" position={Position.Left} className="!border-0 !bg-border-strong" />
      <Handle type="source" position={Position.Right} className="!border-0 !bg-border-strong" />
      <div className="mb-1.5 text-[9px] uppercase tracking-wide text-text-faint">{data.lane}</div>
      <div className="mb-2 flex items-center gap-2">
        <div className={cn("flex h-6 w-6 flex-none items-center justify-center rounded-md text-[10px] font-bold", meta.bgClass, meta.colorClass)}>
          {meta.initial}
        </div>
        <div className="truncate text-[13px] font-semibold text-text">{data.title}</div>
      </div>
      {data.subtitle && <div className="mono mb-1.5 truncate text-[11px] text-text-muted">{data.subtitle}</div>}
      <div className={cn("mono inline-block rounded-full px-2 py-0.5 text-[10px]", TONE_BADGE[data.tone])}>{data.statusLabel}</div>
    </div>
  );
}
