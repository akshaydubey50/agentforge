import type { MemoryEntryOut } from "@/lib/api";
import { formatRelativeTime } from "@/lib/statusPill";
import { cn } from "@/lib/utils";

const KIND_CLASS: Record<string, string> = {
  episodic: "bg-role-supervisor/15 text-role-supervisor",
  fact: "bg-role-reviewer/15 text-role-reviewer",
  preference: "bg-role-human/15 text-role-human",
};

export function MemoryCard({ entry }: { entry: MemoryEntryOut }) {
  const kindClass = KIND_CLASS[entry.kind] ?? "bg-surface-3 text-text-muted";
  return (
    <div className="mb-2.5 rounded-[var(--rm)] border border-border bg-surface px-4.5 py-4">
      <div className="mb-2.5 flex items-center justify-between">
        <span className={cn("rounded-md px-2.5 py-1 text-[10.5px] font-semibold uppercase tracking-wide", kindClass)}>
          {entry.kind}
        </span>
        <span className="flex items-center gap-1 text-[11px] text-text-faint">
          importance{" "}
          <span className="text-role-specialist">
            {"★".repeat(entry.importance)}
            <span className="text-text-faint">{"☆".repeat(Math.max(0, 5 - entry.importance))}</span>
          </span>{" "}
          {entry.importance}/5
        </span>
      </div>
      <div className="mb-2.5 text-[13px] leading-relaxed text-text">{entry.content}</div>
      <div className="mono flex justify-between text-[10.5px] text-text-faint">
        <span>{entry.task_id ? `task_${entry.task_id.slice(0, 8)}` : "no task"}</span>
        <span>last accessed {formatRelativeTime(entry.last_accessed_at)}</span>
      </div>
    </div>
  );
}
