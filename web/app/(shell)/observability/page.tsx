"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import useSWR from "swr";
import { Activity, AlertTriangle, Clock3, DollarSign, RotateCcw, ShieldAlert, Wrench } from "lucide-react";
import { api } from "@/lib/api";
import { TopBar } from "@/components/shell/TopBar";
import { SkeletonRows } from "@/components/ui/Skeleton";
import { cn } from "@/lib/utils";

function successTone(rate: number | null) {
  if (rate === null) return "bg-status-pending";
  if (rate >= 0.9) return "bg-status-completed";
  if (rate >= 0.5) return "bg-status-revision";
  return "bg-status-failed";
}

export default function ObservabilityPage() {
  const { data, isLoading } = useSWR("analytics-full", () => api.getAnalytics(), { refreshInterval: 8000 });
  const { data: summary } = useSWR("system-summary", () => api.getSystemSummary(), { refreshInterval: 5000 });

  if (isLoading && !data) {
    return (
      <>
        <TopBar title="Observability" subtitle="agent-specific runtime operations" />
        <div className="flex-1 overflow-y-auto px-7 py-5.5">
          <SkeletonRows rows={4} />
        </div>
      </>
    );
  }
  if (!data) return null;

  const totalEscalations = Object.values(data.escalations_by_status).reduce((a, b) => a + b, 0);
  const tools = Object.entries(data.tool_stats);

  return (
    <>
      <TopBar title="Observability" subtitle="runs, latency, tokens, tools, approvals, and failures" />
      <div className="flex-1 overflow-y-auto px-7 py-5.5">
        <div className="mb-5 grid grid-cols-4 gap-3 max-[1100px]:grid-cols-2 max-[700px]:grid-cols-1">
          <Kpi icon={<Activity className="h-4 w-4" />} label="Runs" value={data.total_tasks} />
          <Kpi icon={<Wrench className="h-4 w-4" />} label="Tool calls" value={data.total_tool_calls} />
          <Kpi icon={<ShieldAlert className="h-4 w-4" />} label="Approvals" value={totalEscalations} />
          <Kpi icon={<DollarSign className="h-4 w-4" />} label="Cost" value={`$${data.total_cost_usd.toFixed(4)}${data.cost_is_estimated ? " est" : ""}`} />
          <Kpi icon={<AlertTriangle className="h-4 w-4" />} label="Tool failures" value={summary?.tool_calls.failed ?? "Loading"} />
          <Kpi icon={<Clock3 className="h-4 w-4" />} label="Trace spans 24h" value={summary?.trace_spans_24h ?? "Loading"} />
          <Kpi icon={<RotateCcw className="h-4 w-4" />} label="Recovery events" value="Trace detail" />
          <Kpi icon={<ShieldAlert className="h-4 w-4" />} label="Policy denies" value="Gap" />
        </div>

        <section className="mb-5 rounded-[8px] border border-border bg-surface px-5 py-4.5">
          <div className="mb-4 text-[11px] uppercase tracking-wide text-text-faint">Tool latency and success</div>
          <div className="mb-2.5 grid grid-cols-[1.3fr_70px_1fr_90px] gap-3 text-[10px] uppercase tracking-wide text-text-faint">
            <div>Tool</div>
            <div>Calls</div>
            <div>Success rate</div>
            <div className="text-right">Avg latency</div>
          </div>
          {tools.length === 0 && <div className="py-3 text-[12.5px] text-text-faint">No tool calls yet.</div>}
          {tools.map(([name, stats]) => (
            <div key={name} className="grid grid-cols-[1.3fr_70px_1fr_90px] items-center gap-3 border-b border-border py-2.5 text-[12.5px] last:border-b-0">
              <div className="font-mono font-medium text-text">{name}</div>
              <div className="text-text-muted">{stats.calls}</div>
              <div className="h-1.5 overflow-hidden rounded-full bg-canvas">
                <div className={cn("h-full rounded-full", successTone(stats.success_rate))} style={{ width: `${(stats.success_rate ?? 0) * 100}%` }} />
              </div>
              <div className="font-mono text-right text-text-muted">
                {stats.avg_latency_ms === null ? "n/a" : stats.avg_latency_ms < 1000 ? `${stats.avg_latency_ms}ms` : `${(stats.avg_latency_ms / 1000).toFixed(1)}s`}
              </div>
            </div>
          ))}
        </section>

        <section className="rounded-[8px] border border-border bg-rail p-4 text-[12.5px] leading-relaxed text-text-muted">
          Drill-down currently goes through <Link href="/runs" className="text-role-supervisor hover:underline">Runs</Link> and Run Detail. Dedicated anomaly-to-node links require persisted node identifiers in the run graph API.
        </section>
      </div>
    </>
  );
}

function Kpi({ icon, label, value }: { icon: ReactNode; label: string; value: string | number }) {
  return (
    <div className="rounded-[8px] border border-border bg-surface px-4 py-3.5">
      <div className="flex items-center gap-2 text-text-faint">
        {icon}
        <span className="text-[10.5px] uppercase tracking-wide">{label}</span>
      </div>
      <div className="mt-2 font-mono text-[20px] font-semibold text-text">{value}</div>
    </div>
  );
}
