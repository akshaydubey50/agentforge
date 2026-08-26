import type { SubtaskStatus, TaskStatus } from "./api";

// The mockups' shared design system note: "pending=gray, running=amber,
// awaiting_approval=violet[blue], completed=teal[green], failed=red,
// needs_revision=orange" -- backed by the status-* CSS tokens in globals.css.

export const TASK_STATUS_META: Record<TaskStatus, { label: string; dot: string; text: string }> = {
  pending: { label: "pending", dot: "bg-status-pending", text: "text-status-pending" },
  running: { label: "running", dot: "bg-status-running", text: "text-status-running" },
  awaiting_approval: { label: "awaiting_approval", dot: "bg-status-awaiting", text: "text-status-awaiting" },
  completed: { label: "completed", dot: "bg-status-completed", text: "text-status-completed" },
  failed: { label: "failed", dot: "bg-status-failed", text: "text-status-failed" },
  cancelled: { label: "cancelled", dot: "bg-status-pending", text: "text-status-pending" },
};

export const SUBTASK_STATUS_META: Record<SubtaskStatus, { label: string; dot: string; text: string; border: string }> = {
  pending: { label: "pending", dot: "bg-status-pending", text: "text-status-pending", border: "border-border" },
  ready: { label: "ready", dot: "bg-status-pending", text: "text-status-pending", border: "border-border" },
  running: { label: "running", dot: "bg-status-running", text: "text-status-running", border: "border-status-running" },
  needs_revision: { label: "needs revision", dot: "bg-status-revision", text: "text-status-revision", border: "border-status-revision" },
  done: { label: "done", dot: "bg-status-completed", text: "text-status-completed", border: "border-status-completed" },
  escalated: { label: "escalated", dot: "bg-status-awaiting", text: "text-status-awaiting", border: "border-status-awaiting" },
  failed: { label: "failed", dot: "bg-status-failed", text: "text-status-failed", border: "border-status-failed" },
  skipped: { label: "skipped", dot: "bg-status-pending", text: "text-status-pending", border: "border-border" },
};

export function isActiveTaskStatus(status: TaskStatus) {
  return status === "pending" || status === "running";
}

// Matches the backend's _MESSAGEABLE_STATUSES (src/agentsys/main.py):
// a follow-up only makes sense once the agent has actually stopped and
// produced something to react to. awaiting_approval has its own resolution
// path (the escalation gate) instead.
export function canContinueConversation(status: TaskStatus) {
  return status === "completed" || status === "failed";
}
