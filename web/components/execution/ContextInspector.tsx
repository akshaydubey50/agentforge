import type { ContextMetric, ContextMetrics } from "@/lib/execution/types";
import { cn } from "@/lib/utils";

function fmt(value: number | null, suffix = "") {
  return typeof value === "number" ? `${value.toLocaleString()}${suffix}` : "Not exposed";
}

const ROW_META: Record<ContextMetric["status"], string> = {
  included: "text-status-completed",
  reserved: "text-role-human",
  dropped: "text-status-failed",
  unknown: "text-text-faint",
};

export function ContextInspector({ context }: { context: ContextMetrics }) {
  const usedPct =
    context.windowTokens && context.selectedTokens
      ? Math.min(100, Math.round((context.selectedTokens / context.windowTokens) * 100))
      : null;

  return (
    <div className="space-y-3">
      <div className="rounded-[8px] border border-border bg-background/55 p-3">
        <div className="mb-2 flex items-center justify-between">
          <span className="text-[12px] font-semibold text-text">Context window</span>
          <span className="font-mono text-[11px] text-text-faint">{usedPct === null ? "metrics unavailable" : `${usedPct}% selected`}</span>
        </div>
        <div className="h-2 overflow-hidden rounded-full bg-surface-3">
          <div className="h-full bg-role-supervisor" style={{ width: `${usedPct ?? 0}%` }} />
        </div>
        <div className="mt-3 grid grid-cols-2 gap-2 text-[11.5px]">
          <Metric label="Window" value={fmt(context.windowTokens)} />
          <Metric label="Reserve" value={fmt(context.reservedOutputTokens)} />
          <Metric label="Usable" value={fmt(context.usableInputTokens)} />
          <Metric label="Selected" value={fmt(context.selectedTokens)} />
          <Metric label="Avoided" value={fmt(context.tokensAvoided)} />
          <Metric label="Compression" value={fmt(context.compressionRatio, "x")} />
        </div>
      </div>

      <div className="rounded-[8px] border border-border bg-background/55">
        {context.rows.map((row) => (
          <div key={row.label} className="border-b border-border px-3 py-2 last:border-b-0">
            <div className="flex items-center justify-between gap-2">
              <span className="text-[12px] text-text">{row.label}</span>
              <span className={cn("font-mono text-[11px]", ROW_META[row.status])}>
                {row.status} - {fmt(row.tokens)}
              </span>
            </div>
            {row.reason && <div className="mt-0.5 text-[11px] leading-snug text-text-faint">{row.reason}</div>}
          </div>
        ))}
      </div>

      {context.source === "unavailable" && (
        <div className="rounded-[7px] border border-border bg-surface-2 px-3 py-2 text-[11.5px] leading-relaxed text-text-muted">
          The current API exposes durable task state and traces, but not full per-call context composition for every model call.
          This panel only renders safe metadata that is present.
        </div>
      )}
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-[7px] border border-border bg-surface px-2 py-1.5">
      <div className="text-[10px] uppercase tracking-[0.08em] text-text-faint">{label}</div>
      <div className="mt-0.5 font-mono text-[11px] text-text">{value}</div>
    </div>
  );
}
