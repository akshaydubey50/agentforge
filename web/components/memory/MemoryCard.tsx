import type { MemoryEntryOut } from "@/lib/api";
import { formatRelativeTime } from "@/lib/statusPill";
import { cn } from "@/lib/utils";

const KIND_CLASS: Record<string, string> = {
  semantic: "bg-role-reviewer/15 text-role-reviewer",
  episodic: "bg-role-supervisor/15 text-role-supervisor",
  pinned_decision: "bg-status-completed/15 text-status-completed",
  preference: "bg-role-human/15 text-role-human",
  artifact_reference: "bg-status-running/15 text-status-running",
};

export function MemoryCard({ entry }: { entry: MemoryEntryOut }) {
  const kindClass = KIND_CLASS[entry.kind] ?? "bg-surface-3 text-text-muted";
  return (
    <div className="mb-2.5 rounded-[var(--rm)] border border-border bg-surface px-4.5 py-4">
      <div className="mb-2.5 flex items-center justify-between gap-3">
        <span className={cn("rounded-md px-2.5 py-1 text-[10.5px] font-semibold uppercase tracking-wide", kindClass)}>
          {entry.kind}
        </span>
        <span className="flex items-center gap-1 text-[11px] text-text-faint">importance {entry.importance}/5</span>
      </div>
      <div className="mb-2.5 text-[13px] leading-relaxed text-text">{entry.content}</div>
      <div className="mono flex flex-wrap justify-between gap-2 text-[10.5px] text-text-faint">
        <span>provenance {entry.task_id ? `task_${entry.task_id.slice(0, 8)}` : "not linked"}</span>
        <span>created {formatRelativeTime(entry.created_at)}</span>
        <span>last accessed {formatRelativeTime(entry.last_accessed_at)}</span>
      </div>
    </div>
  );
}
