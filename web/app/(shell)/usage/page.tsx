"use client";

import useSWR from "swr";
import { api } from "@/lib/api";
import { TopBar } from "@/components/shell/TopBar";
import { SkeletonRows } from "@/components/ui/Skeleton";
import { purposeLabel } from "@/lib/usageLabels";

export default function UsagePage() {
  const { data, isLoading } = useSWR("analytics", () => api.getAnalytics(), { refreshInterval: 10000 });

  const byPurpose = data ? Object.entries(data.cost_by_purpose).sort((a, b) => b[1] - a[1]) : [];
  const maxCost = byPurpose.length ? byPurpose[0][1] : 1;
  const avgPerTask = data && data.total_tasks > 0 ? data.total_cost_usd / data.total_tasks : 0;

  return (
    <>
      <TopBar title="Usage" />
      <div className="flex-1 overflow-y-auto px-5 py-5">
        {isLoading && <SkeletonRows />}
        {data && (
          <>
            <div className="mb-4 grid grid-cols-4 gap-2.5">
              <Kpi label="Spent" value={`$${data.total_cost_usd.toFixed(2)}`} />
              <Kpi label="Tasks" value={String(data.total_tasks)} />
              <Kpi label="Avg / task" value={`$${avgPerTask.toFixed(4)}`} />
              <Kpi label="Tool calls" value={String(data.total_tool_calls)} />
            </div>

            <h3 className="mb-2.5 mt-0 text-[11px] font-extrabold uppercase tracking-wide text-text-faint">
              Where it goes
            </h3>
            <div className="rounded-[var(--rm)] border border-border bg-surface p-3.5">
              {byPurpose.length === 0 && <div className="text-[13px] text-text-muted">No costed calls yet.</div>}
              {byPurpose.map(([purpose, cost]) => (
                <div key={purpose} className="flex items-center gap-2.5 py-1">
                  <div className="w-[140px] flex-none text-[12px] text-text-muted">{purposeLabel(purpose)}</div>
                  <div className="h-[9px] flex-1 rounded-full bg-surface-3">
                    <div
                      className="h-full rounded-full bg-brand transition-[width] duration-700"
                      style={{ width: `${(cost / maxCost) * 100}%` }}
                    />
                  </div>
                  <div className="w-[66px] text-right text-[11.5px] tabular-nums text-text-muted">
                    ${cost.toFixed(4)}
                  </div>
                </div>
              ))}
            </div>
            <p className="mt-2.5 text-[11.5px] text-text-faint">
              &quot;Checking answers&quot; is the reviewer double-checking work before you see it —
              the main reason answers are trustworthy.
            </p>
          </>
        )}
      </div>
    </>
  );
}

function Kpi({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-[var(--rm)] border border-border bg-surface px-3.5 py-3">
      <div className="text-[10px] font-extrabold uppercase tracking-wide text-text-faint">{label}</div>
      <div className="font-serif-display mt-1 text-[22px] font-semibold tabular-nums text-text">{value}</div>
    </div>
  );
}
