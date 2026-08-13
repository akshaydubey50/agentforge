"use client";

import useSWR from "swr";
import { api } from "@/lib/api";
import { TASK_STATUS_META } from "@/lib/agentStatus";
import { SkeletonRows } from "@/components/ui/Skeleton";
import { cn } from "@/lib/utils";

function successTone(rate: number | null) {
  if (rate === null) return "bg-status-pending";
  if (rate >= 0.9) return "bg-status-completed";
  if (rate >= 0.5) return "bg-status-revision";
  return "bg-status-failed";
}

export default function AnalyticsPage() {
  const { data, isLoading } = useSWR("analytics-full", () => api.getAnalytics(), { refreshInterval: 8000 });

  if (isLoading && !data) {
    return (
      <div className="flex-1 overflow-y-auto px-7 py-5.5">
        <SkeletonRows rows={4} />
      </div>
    );
  }
  if (!data) return null;

  const totalEscalations = Object.values(data.escalations_by_status).reduce((a, b) => a + b, 0);
  const tools = Object.entries(data.tool_stats);
  const purposes = Object.entries(data.cost_by_purpose).sort((a, b) => b[1] - a[1]);
  const maxCost = Math.max(...purposes.map(([, v]) => v), 0.000001);
  const statusEntries = (Object.keys(TASK_STATUS_META) as (keyof typeof TASK_STATUS_META)[])
    .map((k) => [k, data.tasks_by_status[k] ?? 0] as const)
    .filter(([, count]) => count > 0);

  return (
    <div className="flex-1 overflow-y-auto px-7 py-5.5">
      <h1 className="mb-1.5 text-[17px] font-semibold text-text">Analytics</h1>
      <p className="mb-5.5 text-[12.5px] text-text-faint">
        Cost &amp; performance from /v1/analytics — aggregated across all tasks, tool calls, and LLM calls.
      </p>

      <div className="mb-5.5 grid grid-cols-4 gap-3">
        <Kpi label="Total tasks" value={data.total_tasks} />
        <Kpi label="Tool calls" value={data.total_tool_calls} />
        <Kpi label="Escalations" value={totalEscalations} />
        <Kpi label="Total cost" value={`$${data.total_cost_usd.toFixed(4)}`} tone="text-status-completed" />
      </div>

      <div className="mb-4 grid grid-cols-[1.4fr_1fr] gap-4">
        <div className="rounded-[var(--rm)] border border-border bg-surface px-5 py-4.5">
          <div className="mb-4 text-[11px] uppercase tracking-wide text-text-faint">Tool performance</div>
          <div className="mb-2.5 grid grid-cols-[1.3fr_70px_1fr_80px] gap-3 text-[10px] uppercase tracking-wide text-text-faint">
            <div>tool</div>
            <div>calls</div>
            <div>success rate</div>
            <div className="text-right">avg latency</div>
          </div>
          {tools.length === 0 && <div className="py-3 text-[12.5px] text-text-faint">No tool calls yet.</div>}
          {tools.map(([name, stats]) => (
            <div key={name} className="grid grid-cols-[1.3fr_70px_1fr_80px] items-center gap-3 border-b border-border py-2.5 text-[12.5px] last:border-b-0">
              <div className="mono font-medium text-text">{name}</div>
              <div className="text-text-muted">{stats.calls}</div>
              <div className="h-1.5 overflow-hidden rounded-full bg-canvas">
                <div
                  className={cn("h-full rounded-full", successTone(stats.success_rate))}
                  style={{ width: `${(stats.success_rate ?? 0) * 100}%` }}
                />
              </div>
              <div className="mono text-right text-text-muted">
                {stats.avg_latency_ms === null ? "—" : stats.avg_latency_ms < 1000 ? `${stats.avg_latency_ms}ms` : `${(stats.avg_latency_ms / 1000).toFixed(1)}s`}
              </div>
            </div>
          ))}
        </div>

        <div className="rounded-[var(--rm)] border border-border bg-surface px-5 py-4.5">
          <div className="mb-4 text-[11px] uppercase tracking-wide text-text-faint">Cost by purpose</div>
          {purposes.length === 0 && <div className="py-3 text-[12.5px] text-text-faint">No LLM calls yet.</div>}
          {purposes.map(([purpose, cost]) => (
            <div key={purpose} className="flex items-center justify-between gap-3 border-b border-border py-2.5 text-[12.5px] last:border-b-0">
              <span className="mono w-[110px] flex-none truncate text-text-muted">{purpose}</span>
              <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-canvas">
                <div className="h-full rounded-full bg-role-supervisor" style={{ width: `${(cost / maxCost) * 100}%` }} />
              </div>
              <span className="mono flex-none text-text">${cost.toFixed(4)}</span>
            </div>
          ))}
        </div>
      </div>

      <div className="rounded-[var(--rm)] border border-border bg-surface px-5 py-4.5">
        <div className="mb-4 text-[11px] uppercase tracking-wide text-text-faint">Tasks by status</div>
        {statusEntries.length === 0 ? (
          <div className="text-[12.5px] text-text-faint">No tasks yet.</div>
        ) : (
          <>
            <div className="mb-3.5 flex h-[30px] overflow-hidden rounded-md">
              {statusEntries.map(([status, count]) => (
                <div
                  key={status}
                  className={cn("flex items-center justify-center text-[11px] font-semibold text-badT", TASK_STATUS_META[status].dot)}
                  style={{ width: `${(count / data.total_tasks) * 100}%` }}
                >
                  {count > 0 && `${status} · ${count}`}
                </div>
              ))}
            </div>
            <div className="flex flex-wrap gap-x-4 gap-y-2">
              {statusEntries.map(([status, count]) => (
                <span key={status} className="flex items-center gap-1.5 text-[11.5px] text-text-muted">
                  <span className={cn("h-[9px] w-[9px] rounded-sm", TASK_STATUS_META[status].dot)} />
                  {status} {count}
                </span>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function Kpi({ label, value, tone }: { label: string; value: string | number; tone?: string }) {
  return (
    <div className="rounded-[var(--rm)] border border-border bg-surface px-4.5 py-4">
      <div className="text-[10.5px] uppercase tracking-wide text-text-faint">{label}</div>
      <div className={cn("mono mt-1.5 text-[22px] font-semibold", tone ?? "text-text")}>{value}</div>
    </div>
  );
}
