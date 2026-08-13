import Link from "next/link";
import type { TaskOut } from "@/lib/api";
import { isActiveTaskStatus } from "@/lib/agentStatus";
import { cn } from "@/lib/utils";

export function TaskDetailHeader({ task, view }: { task: TaskOut; view: "feed" | "graph" }) {
  const live = isActiveTaskStatus(task.status);
  return (
    <div className="flex items-start justify-between border-b border-border px-5.5 py-4">
      <div>
        <div className="text-[15px] font-semibold text-text">{task.request_text}</div>
        <div className="mono mt-0.5 text-[11.5px] text-text-faint">task_{task.id.slice(0, 8)}</div>
      </div>
      <div className="flex flex-none items-center gap-3">
        <div className="flex overflow-hidden rounded-lg border border-border bg-surface text-[12px]">
          <Link
            href={`/tasks/${task.id}`}
            className={cn("px-3 py-1.5 text-text-muted", view === "feed" && "bg-surface-3 text-text")}
          >
            Feed
          </Link>
          <Link
            href={`/tasks/${task.id}/graph`}
            className={cn("px-3 py-1.5 text-text-muted", view === "graph" && "bg-surface-3 text-text")}
          >
            Graph
          </Link>
        </div>
        {live && (
          <div className="flex items-center gap-1.5 rounded-full bg-status-running/10 px-3 py-1.5 text-[11.5px] text-status-running">
            <span className="animate-now-pulse h-1.5 w-1.5 rounded-full bg-status-running" />
            {task.status}
          </div>
        )}
      </div>
    </div>
  );
}
