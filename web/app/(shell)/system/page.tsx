"use client";

// The System view -- what AgentForge IS, with live numbers on it.
//
// The README's architecture diagram has always been the clearest explanation
// of this system and it appeared nowhere in the product. This page is that
// diagram, except every box is drawn from the compiled graph, carries its own
// 24h activity, and links through to the live data behind it.

import { useState } from "react";
import Link from "next/link";
import useSWR from "swr";
import { api } from "@/lib/api";
import { ArchitectureMap } from "@/components/system/ArchitectureMap";
import { SkeletonRows } from "@/components/ui/Skeleton";
import { cn } from "@/lib/utils";

function Kpi({
  label,
  value,
  sub,
  tone,
  href,
}: {
  label: string;
  value: string | number;
  sub?: string;
  tone?: string;
  href?: string;
}) {
  const body = (
    <>
      <div className={cn("text-[19px] font-semibold tabular-nums text-text", tone)}>{value}</div>
      <div className="mt-0.5 text-[11.5px] text-text-faint">{label}</div>
      {sub && <div className="mt-1 text-[11px] text-text-muted">{sub}</div>}
    </>
  );
  const className =
    "rounded-[var(--rm)] border border-border bg-surface px-4 py-3.5 transition-colors";
  return href ? (
    <Link href={href} className={cn(className, "hover:border-border-strong")}>
      {body}
    </Link>
  ) : (
    <div className={className}>{body}</div>
  );
}

export default function SystemPage() {
  const [selected, setSelected] = useState<string | null>(null);

  // Topology describes code and config, so it is fetched once and never
  // revalidated on a timer; only the counts are live.
  const { data: topology, isLoading } = useSWR("system-topology", () => api.getSystemTopology(), {
    revalidateOnFocus: false,
  });
  const { data: summary } = useSWR("system-summary", () => api.getSystemSummary(), {
    refreshInterval: 5000,
  });

  if (isLoading && !topology) {
    return (
      <div className="flex-1 overflow-y-auto px-7 py-5.5">
        <SkeletonRows rows={5} />
      </div>
    );
  }
  if (!topology) return null;

  const spend = summary?.spend;
  const toolCalls = summary?.tool_calls;
  const failureRate =
    toolCalls && toolCalls.total > 0 ? Math.round((toolCalls.failed / toolCalls.total) * 100) : 0;

  return (
    <div className="flex-1 overflow-y-auto px-7 py-5.5">
      <div className="mb-1.5 flex items-center gap-2.5">
        <h1 className="text-[17px] font-semibold text-text">System</h1>
        <span className="flex items-center gap-1.5 text-[11px] text-text-faint">
          <span className="h-1.5 w-1.5 rounded-full bg-status-completed" />
          live
        </span>
      </div>
      <p className="mb-5.5 max-w-[78ch] text-[12.5px] leading-relaxed text-text-faint">
        Every box below is read off the compiled LangGraph, the live tool registry, and the running
        config — not a drawing. Click a node to see what it does and jump to its data.
      </p>

      <div className="mb-5.5 grid grid-cols-6 gap-3">
        <Kpi label="Tasks" value={summary?.tasks.total ?? "—"} sub={`${summary?.tasks.active ?? 0} active`} href="/tasks" />
        <Kpi
          label="Awaiting approval"
          value={summary?.approvals_pending ?? "—"}
          tone={summary?.approvals_pending ? "text-status-awaiting" : undefined}
          href="/approvals"
        />
        <Kpi label="Steps run" value={summary?.steps_run ?? "—"} href="/runs" />
        <Kpi
          label="Tool calls"
          value={toolCalls?.total ?? "—"}
          sub={toolCalls?.total ? `${failureRate}% failed` : undefined}
          tone={failureRate > 25 ? "text-status-failed" : undefined}
        />
        <Kpi
          label="Spend"
          value={spend ? `$${spend.usd.toFixed(4)}` : "—"}
          sub={spend ? `${spend.llm_calls} LLM calls` : undefined}
          tone="text-status-completed"
          href="/usage"
        />
        <Kpi label="Spans · 24h" value={summary?.trace_spans_24h ?? "—"} href="/runs" />
      </div>

      <div className="mb-2.5 text-[11px] uppercase tracking-wide text-text-faint">Execution graph</div>
      <div className="mb-6">
        <ArchitectureMap
          nodes={topology.graph.nodes}
          edges={topology.graph.edges}
          summary={summary}
          selectedId={selected}
          onSelect={(id) => setSelected((current) => (current === id ? null : id))}
        />
      </div>

      <div className="mb-2.5 text-[11px] uppercase tracking-wide text-text-faint">Subsystems</div>
      <div className="mb-6 grid grid-cols-3 gap-3">
        {topology.subsystems.map((sub) => {
          const body = (
            <>
              <div className="flex items-baseline justify-between gap-2">
                <span className="text-[13px] font-semibold text-text">{sub.label}</span>
                {sub.href && <span className="text-[11px] text-text-faint">→</span>}
              </div>
              <p className="mt-1 text-[12px] leading-relaxed text-text-muted">{sub.summary}</p>
              <dl className="mt-3 space-y-1.5 border-t border-border pt-2.5">
                {sub.facts.map((fact) => (
                  <div key={fact.label} className="flex items-baseline justify-between gap-3">
                    <dt className="flex-none text-[11px] uppercase tracking-[0.06em] text-text-faint">
                      {fact.label}
                    </dt>
                    <dd className="min-w-0 truncate text-right font-mono text-[11.5px] text-text">
                      {fact.value}
                      {fact.note && (
                        <span className="ml-1.5 font-sans text-[10.5px] text-text-faint">{fact.note}</span>
                      )}
                    </dd>
                  </div>
                ))}
              </dl>
            </>
          );
          const shell = "rounded-[var(--rm)] border border-border bg-surface px-4 py-3.5";
          // Config-only subsystems render as plain cards. Sending someone to
          // a disabled mockup would imply these are editable there.
          return sub.href ? (
            <Link key={sub.id} href={sub.href} className={cn(shell, "transition-colors hover:border-border-strong")}>
              {body}
            </Link>
          ) : (
            <div key={sub.id} className={shell}>
              {body}
            </div>
          );
        })}
      </div>

      <div className="mb-2.5 flex items-baseline justify-between">
        <span className="text-[11px] uppercase tracking-wide text-text-faint">
          Tools · {topology.tools.length} registered
        </span>
        <span className="text-[11px] text-text-faint">
          the live registry — a tool whose dependency is missing degrades out and is absent here
        </span>
      </div>
      <div className="grid grid-cols-2 gap-2.5 xl:grid-cols-3">
        {topology.tools.map((tool) => (
          <div
            key={tool.name}
            className="rounded-[var(--rs)] border border-border bg-surface px-3.5 py-3"
          >
            <div className="flex items-center gap-2">
              <span className="truncate font-mono text-[12px] text-text">{tool.name}</span>
              {tool.requires_approval && (
                <span className="flex-none rounded-full bg-warn-wash px-1.5 py-0.5 text-[10px] font-medium text-warn">
                  approval
                </span>
              )}
              {tool.server && (
                <span className="flex-none rounded-full bg-brand-wash px-1.5 py-0.5 text-[10px] font-medium text-brand">
                  mcp
                </span>
              )}
            </div>
            <p className="mt-1 line-clamp-2 text-[11.5px] leading-relaxed text-text-faint">
              {tool.summary}
            </p>
          </div>
        ))}
      </div>
    </div>
  );
}
