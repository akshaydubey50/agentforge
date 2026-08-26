"use client";

import Link from "next/link";
import useSWR from "swr";
import { api, type TaskOut } from "@/lib/api";
import { TASK_STATUS_META, isActiveTaskStatus } from "@/lib/agentStatus";
import { formatRelativeTime } from "@/lib/statusPill";
import { cn } from "@/lib/utils";

export function TaskRow({ task }: { task: TaskOut }) {
  const { data: detail } = useSWR(["task-row", task.id], () => api.getTask(task.id), {
    refreshInterval: isActiveTaskStatus(task.status) ? 4000 : 0,
  });

  const meta = TASK_STATUS_META[task.status];
  const subtasks = detail?.subtasks ?? [];
  const doneCount = subtasks.filter((s) => s.status === "done").length;
  const current = [...subtasks].reverse().find((s) => s.status !== "done" && s.status !== "skipped") ?? subtasks[subtasks.length - 1];
  const currentLabel =
    task.status === "completed"
      ? "synthesized"
      : current
        ? `#${current.position} ${current.assigned_tool ?? "model output"}`
        : "—";

  return (
    <Link
      href={`/tasks/${task.id}`}
      className="grid grid-cols-[18px_2fr_1.2fr_1fr_90px_90px] items-center gap-3.5 border-b border-border px-4.5 py-3 text-[13px] last:border-b-0 hover:bg-surface-3/40"
    >
      <span className={cn("h-2 w-2 flex-none rounded-full", meta.dot, task.status === "running" && "animate-now-pulse")} />
      <div className="min-w-0">
        <div className="truncate font-medium text-text">{task.request_text}</div>
        <div className="mono mt-0.5 text-[10.5px] text-text-faint">task_{task.id.slice(0, 8)}</div>
      </div>
      <div className="mono truncate text-[11.5px] text-text-muted">{currentLabel}</div>
      <div className={cn("text-[12px] font-semibold", meta.text)}>{meta.label}</div>
      <div className="mono text-[12px] text-text-muted">
        {subtasks.length > 0 ? `${doneCount}/${subtasks.length} done` : "—"}
      </div>
      <div className="mono text-right text-[11.5px] text-text-faint">{formatRelativeTime(task.updated_at)}</div>
    </Link>
  );
}
