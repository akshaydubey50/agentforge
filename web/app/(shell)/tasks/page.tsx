"use client";

import { useState } from "react";
import useSWR from "swr";
import { api, type TaskStatus } from "@/lib/api";
import { TaskComposer } from "@/components/tasks/TaskComposer";
import { TaskRow } from "@/components/tasks/TaskRow";
import { SkeletonRows } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/ui/EmptyState";
import { Pagination } from "@/components/ui/Pagination";
import { cn } from "@/lib/utils";

const FILTERS: { value: TaskStatus | "all"; label: string }[] = [
  { value: "all", label: "all" },
  { value: "running", label: "running" },
  { value: "awaiting_approval", label: "awaiting_approval" },
  { value: "completed", label: "completed" },
  { value: "failed", label: "failed" },
];

export default function TasksPage() {
  const [status, setStatus] = useState<TaskStatus | "all">("all");
  const [offset, setOffset] = useState(0);
  const [limit, setLimit] = useState(25);

  const { data, isLoading } = useSWR(["tasks", status, offset, limit], () => api.listTasks(limit, offset, status), {
    refreshInterval: 5000,
  });
  const { data: analytics } = useSWR("analytics-summary", () => api.getAnalytics(), { refreshInterval: 8000 });

  const tasks = data?.items ?? [];
  const total = data?.total ?? 0;
  const byStatus = analytics?.tasks_by_status ?? {};

  return (
    <div className="flex-1 overflow-y-auto px-7 py-5.5">
      <div className="mb-5 flex items-center justify-between">
        <h1 className="text-[17px] font-semibold text-text">Tasks</h1>
      </div>

      <TaskComposer />

      <div className="mb-4.5 grid grid-cols-5 gap-3">
        <SummaryCard label="Running" value={byStatus.running ?? 0} tone="text-status-running" />
        <SummaryCard label="Awaiting approval" value={byStatus.awaiting_approval ?? 0} tone="text-status-awaiting" />
        <SummaryCard label="Completed" value={byStatus.completed ?? 0} tone="text-status-completed" />
        <SummaryCard label="Failed" value={byStatus.failed ?? 0} tone="text-status-failed" />
        <SummaryCard
          label="Total cost"
          value={analytics ? `$${analytics.total_cost_usd.toFixed(4)}` : "—"}
          tone="text-text"
        />
      </div>

      <div className="mb-3 flex flex-wrap gap-1.5">
        {FILTERS.map((f) => (
          <button
            key={f.value}
            onClick={() => {
              setStatus(f.value);
              setOffset(0);
            }}
            className={cn(
              "mono rounded-md border border-border px-3 py-1.5 text-[11.5px] text-text-muted",
              status === f.value && "border-role-supervisor bg-surface-3 text-text"
            )}
          >
            {f.label}
          </button>
        ))}
      </div>

      {isLoading && !data && <SkeletonRows rows={5} />}

      {data && tasks.length === 0 && (
        <EmptyState glyph="▤" title="No tasks yet" description="Submit a request above to start the agent loop." />
      )}

      {tasks.length > 0 && (
        <div className="overflow-hidden rounded-[var(--rm)] border border-border bg-surface">
          <div className="grid grid-cols-[18px_2fr_1.2fr_1fr_90px_90px] gap-3.5 border-b border-border px-4.5 py-2.5 text-[10px] uppercase tracking-wide text-text-faint">
            <div />
            <div>request</div>
            <div>current subtask</div>
            <div>status</div>
            <div>subtasks</div>
            <div className="text-right">updated</div>
          </div>
          {tasks.map((t) => (
            <TaskRow key={t.id} task={t} />
          ))}
        </div>
      )}

      {total > 0 && <Pagination offset={offset} limit={limit} total={total} onOffsetChange={setOffset} onLimitChange={(l) => { setLimit(l); setOffset(0); }} />}
    </div>
  );
}

function SummaryCard({ label, value, tone }: { label: string; value: string | number; tone: string }) {
  return (
    <div className="rounded-[var(--rm)] border border-border bg-surface px-4 py-3.5">
      <div className="text-[10.5px] uppercase tracking-wide text-text-faint">{label}</div>
      <div className={cn("mono mt-1.5 text-[18px] font-semibold", tone)}>{value}</div>
    </div>
  );
}
