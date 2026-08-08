"use client";

import { useState } from "react";
import useSWR from "swr";
import { api, type TaskStatus } from "@/lib/api";
import { TopBar } from "@/components/shell/TopBar";
import { RunsTable } from "@/components/runs/RunsTable";
import { SkeletonRows } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/ui/EmptyState";
import { Pagination } from "@/components/ui/Pagination";

const STATUS_OPTIONS: { value: TaskStatus | "all"; label: string }[] = [
  { value: "all", label: "All statuses" },
  { value: "pending", label: "Queued" },
  { value: "running", label: "Running" },
  { value: "awaiting_approval", label: "Needs you" },
  { value: "completed", label: "Done" },
  { value: "failed", label: "Failed" },
];

export default function RunsPage() {
  const [offset, setOffset] = useState(0);
  const [limit, setLimit] = useState(25);
  const [status, setStatus] = useState<TaskStatus | "all">("all");

  const { data, isLoading } = useSWR(
    ["tasks", offset, limit, status],
    () => api.listTasks(limit, offset, status),
    { refreshInterval: 4000 }
  );
  const tasks = data?.items ?? [];
  const total = data?.total ?? 0;

  return (
    <>
      <TopBar
        title="Runs"
        subtitle="everything the assistant has done"
        actions={
          <select
            className="rounded-[var(--rs)] border border-border-strong bg-background px-2.5 py-1.5 text-[12.5px] text-text"
            value={status}
            onChange={(e) => {
              setStatus(e.target.value as TaskStatus | "all");
              setOffset(0);
            }}
          >
            {STATUS_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        }
      />
      <div className="flex-1 overflow-y-auto px-5 py-5">
        {isLoading && !data && <SkeletonRows />}
        {data && total === 0 && (
          <EmptyState
            glyph="◷"
            title={status === "all" ? "No runs yet" : "Nothing here"}
            description={
              status === "all"
                ? "Ask the assistant something and it'll show up here."
                : "No runs currently match this status."
            }
          />
        )}
        {data && total > 0 && (
          <>
            <RunsTable tasks={tasks} />
            <Pagination
              offset={offset}
              limit={limit}
              total={total}
              onOffsetChange={setOffset}
              onLimitChange={(l) => {
                setLimit(l);
                setOffset(0);
              }}
            />
          </>
        )}
      </div>
    </>
  );
}
