import type { TraceSpanOut } from "@/lib/api";
import { Pill } from "@/components/ui/Pill";
import { humanizeValue } from "@/lib/formatValue";

function summarize(value: unknown, max = 220): string {
  // humanizeValue, not JSON.stringify: a trace row is read by a person, and
  // a wall of braces and escaped quotes is not a summary of anything.
  const text = typeof value === "string" ? value : humanizeValue(value);
  if (!text || text === "—") return "";
  const flat = text.replace(/\s*\n\s*/g, " · ").trim();
  return flat.length > max ? `${flat.slice(0, max)}…` : flat;
}

const SPAN_LABEL: Record<string, string> = {
  supervisor_decompose: "Planned the work",
  specialist_choose_tool: "Chose a tool",
  specialist_reason: "Reasoned through it",
  reviewer_validate: "Checked the work",
  supervisor_synthesize: "Wrote the answer",
  escalation_created: "Flagged for review",
};

interface TimedSpan extends TraceSpanOut {
  startMs: number;
  endMs: number;
}

/** Groups spans whose [start,end] ranges overlap -- this is the literal
 * "ran at the same time" relationship, computed from real timestamps, not
 * inferred from span type or position. */
function clusterByOverlap(spans: TimedSpan[]): TimedSpan[][] {
  const sorted = [...spans].sort((a, b) => a.startMs - b.startMs);
  const clusters: TimedSpan[][] = [];
  let current: TimedSpan[] = [];
  let currentMaxEnd = -Infinity;

  for (const span of sorted) {
    if (current.length === 0 || span.startMs < currentMaxEnd) {
      current.push(span);
      currentMaxEnd = Math.max(currentMaxEnd, span.endMs);
    } else {
      clusters.push(current);
      current = [span];
      currentMaxEnd = span.endMs;
    }
  }
  if (current.length > 0) clusters.push(current);
  return clusters;
}

function SpanRow({ span, t0, total, compact }: { span: TimedSpan; t0: number; total: number; compact?: boolean }) {
  const leftPct = ((span.startMs - t0) / total) * 100;
  const widthPct = Math.max(((span.endMs - span.startMs) / total) * 100, 0.5);
  const offsetS = ((span.startMs - t0) / 1000).toFixed(1);
  const isToolCall = span.span_type === "tool_call";

  return (
    <div className="flex gap-2.5 border-b border-border py-2 last:border-b-0">
      <div className="w-[52px] flex-none pt-0.5 text-[11px] tabular-nums text-text-faint">{offsetS}s</div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5 text-[12.5px] font-semibold text-text">
          {SPAN_LABEL[span.name] ?? span.name}
          {isToolCall && <Pill kind={span.status === "ok" ? "ok" : "bad"}>{span.status}</Pill>}
        </div>
        <div className="mt-0.5 text-[11.5px] text-text-muted">{span.span_type.replace(/_/g, " ")}</div>
        {Object.keys(span.output ?? {}).length > 0 && (
          <div className="mono mt-1.5 overflow-x-auto whitespace-pre-wrap break-words rounded-lg border border-border bg-surface-2 px-2.5 py-2 text-[11px]">
            {summarize(span.output)}
          </div>
        )}
        <div className={`relative mt-1.5 rounded bg-surface-3 ${compact ? "h-1" : "h-1.5"}`}>
          <i
            className="absolute h-full rounded"
            style={{
              background: span.status === "error" ? "var(--bad)" : "var(--ac)",
              left: `${leftPct}%`,
              width: `${widthPct}%`,
            }}
          />
        </div>
      </div>
    </div>
  );
}

export function TraceTimeline({ spans }: { spans: TraceSpanOut[] }) {
  if (spans.length === 0) return null;

  const timed: TimedSpan[] = spans.map((s) => ({
    ...s,
    startMs: new Date(s.started_at).getTime(),
    endMs: new Date(s.ended_at ?? s.started_at).getTime(),
  }));
  const t0 = Math.min(...timed.map((s) => s.startMs));
  const t1 = Math.max(...timed.map((s) => s.endMs), ...timed.map((s) => s.startMs));
  const total = Math.max(t1 - t0, 1);

  const clusters = clusterByOverlap(timed);

  return (
    <div className="rounded-[var(--rm)] border border-border bg-surface px-4">
      {clusters.map((cluster, i) =>
        cluster.length === 1 ? (
          <SpanRow key={cluster[0].id} span={cluster[0]} t0={t0} total={total} />
        ) : (
          <div key={i} className="border-b border-border py-2 pl-2 last:border-b-0" style={{ borderLeftWidth: 2, borderLeftColor: "var(--run)" }}>
            <div className="mb-0.5 pl-2 text-[10.5px] font-bold uppercase tracking-wide text-run">
              {cluster.length} steps ran at the same time
            </div>
            <div className="rounded-md bg-run-wash/40">
              {cluster.map((span) => (
                <SpanRow key={span.id} span={span} t0={t0} total={total} compact />
              ))}
            </div>
          </div>
        )
      )}
    </div>
  );
}
