import type { TaskStatus } from "./api";

export function taskStatusPill(status: TaskStatus): { kind: "ok" | "run" | "wn" | "bad" | "off"; label: string } {
  switch (status) {
    case "completed":
      return { kind: "ok", label: "Done" };
    case "running":
      return { kind: "run", label: "Running" };
    case "awaiting_approval":
      return { kind: "wn", label: "Needs you" };
    case "failed":
      return { kind: "bad", label: "Failed" };
    default:
      return { kind: "off", label: "Queued" };
  }
}

export function formatRelativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  const diffMs = Date.now() - then;
  const mins = Math.round(diffMs / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  return `${days}d ago`;
}
