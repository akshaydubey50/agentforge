"use client";

// The architecture diagram, drawn from /v1/system/topology.
//
// Edges are SVG; node cards are real HTML positioned on top of it. That split
// is deliberate -- foreignObject text rendering is inconsistent across
// browsers and loses focus rings, truncation and hover semantics, all of
// which this needs because every node is a link into that subsystem's live
// data. SVG does the one thing it is better at (curves), HTML does the rest.

import Link from "next/link";
import { useMemo } from "react";
import type { SystemSummary, TopologyEdge, TopologyNode } from "@/lib/api";
import { layoutTopology, type PlacedNode } from "@/lib/systemLayout";
import { cn } from "@/lib/utils";

const ROLE_ACCENT: Record<string, string> = {
  supervisor: "var(--role-supervisor)",
  specialist: "var(--role-specialist)",
  reviewer: "var(--role-reviewer)",
  human: "var(--role-human)",
};

function activityFor(node: TopologyNode, summary?: SystemSummary) {
  if (!node.span_type || !summary) return null;
  return summary.activity_24h?.[node.span_type] ?? { runs: 0, errors: 0 };
}

function NodeCard({
  node,
  summary,
  selected,
  onSelect,
}: {
  node: PlacedNode;
  summary?: SystemSummary;
  selected: boolean;
  onSelect: (id: string) => void;
}) {
  const accent = ROLE_ACCENT[node.role] ?? "var(--role-supervisor)";
  const activity = activityFor(node, summary);

  if (node.terminal) {
    return (
      <div
        style={{ left: node.x, top: node.y, width: node.w, height: node.h }}
        className="absolute flex items-center justify-center rounded-full border border-dashed border-border-strong bg-surface-2 text-[11px] font-medium uppercase tracking-[0.08em] text-text-faint"
      >
        {node.label}
      </div>
    );
  }

  return (
    <button
      type="button"
      onClick={() => onSelect(node.id)}
      style={{ left: node.x, top: node.y, width: node.w, height: node.h }}
      className={cn(
        "absolute flex flex-col justify-center gap-1 overflow-hidden rounded-[12px] border bg-surface px-3 text-left transition-all",
        "hover:border-border-strong hover:shadow-[0_6px_20px_rgba(0,0,0,0.55)]",
        selected ? "border-brand ring-1 ring-brand" : "border-border"
      )}
    >
      <span className="absolute inset-y-0 left-0 w-[3px]" style={{ background: accent }} />
      <span className="text-[13px] font-semibold leading-tight text-text">{node.label}</span>
      <span className="text-[10px] uppercase tracking-[0.09em] text-text-faint">{node.kind}</span>
      {activity && (
        <span className="text-[11px] tabular-nums text-text-muted">
          {activity.runs} {activity.runs === 1 ? "run" : "runs"}
          {activity.errors > 0 && <span className="text-bad"> · {activity.errors} err</span>}
          <span className="text-text-faint"> · 24h</span>
        </span>
      )}
    </button>
  );
}

export function ArchitectureMap({
  nodes,
  edges,
  summary,
  selectedId,
  onSelect,
}: {
  nodes: TopologyNode[];
  edges: TopologyEdge[];
  summary?: SystemSummary;
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  const layout = useMemo(() => layoutTopology(nodes, edges), [nodes, edges]);
  const selected = layout.nodes.find((n) => n.id === selectedId) ?? null;

  return (
    <div className="rounded-[14px] border border-border bg-canvas">
      <div className="overflow-x-auto p-1">
        <div className="relative mx-auto" style={{ width: layout.width, height: layout.height }}>
          <svg
            width={layout.width}
            height={layout.height}
            className="absolute inset-0"
            aria-hidden
          >
            <defs>
              <marker
                id="af-arrow"
                viewBox="0 0 8 8"
                refX="7"
                refY="4"
                markerWidth="7"
                markerHeight="7"
                orient="auto-start-reverse"
              >
                <path d="M 0 0 L 8 4 L 0 8 z" fill="var(--bd2)" />
              </marker>
            </defs>
            {layout.edges.map((edge) => {
              const touchesSelected =
                !!selectedId && (edge.source === selectedId || edge.target === selectedId);
              return (
                <path
                  key={`${edge.source}->${edge.target}`}
                  d={edge.path}
                  fill="none"
                  stroke={touchesSelected ? "var(--ac)" : "var(--bd2)"}
                  strokeWidth={touchesSelected ? 2 : 1.5}
                  // Dashed means "a router decided this" -- the single most
                  // important distinction on the diagram, so it is encoded in
                  // the line itself rather than a label you have to read.
                  strokeDasharray={edge.conditional ? "4 3" : undefined}
                  markerEnd="url(#af-arrow)"
                  opacity={selectedId && !touchesSelected ? 0.35 : 1}
                />
              );
            })}
          </svg>

          {layout.nodes.map((node) => (
            <NodeCard
              key={node.id}
              node={node}
              summary={summary}
              selected={node.id === selectedId}
              onSelect={onSelect}
            />
          ))}
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 border-t border-border px-4 py-2.5 text-[11px] text-text-faint">
        <span className="flex items-center gap-1.5">
          <svg width="22" height="6" aria-hidden>
            <line x1="0" y1="3" x2="22" y2="3" stroke="var(--bd2)" strokeWidth="1.5" />
          </svg>
          always
        </span>
        <span className="flex items-center gap-1.5">
          <svg width="22" height="6" aria-hidden>
            <line x1="0" y1="3" x2="22" y2="3" stroke="var(--bd2)" strokeWidth="1.5" strokeDasharray="4 3" />
          </svg>
          router decides
        </span>
        <span className="ml-auto">
          rendered from the compiled graph — this picture cannot drift from the code
        </span>
      </div>

      {selected && (
        <div className="border-t border-border px-4 py-3.5">
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <span
                  className="h-2 w-2 flex-none rounded-full"
                  style={{ background: ROLE_ACCENT[selected.role] }}
                />
                <span className="text-[13px] font-semibold text-text">{selected.label}</span>
                <span className="text-[11px] uppercase tracking-[0.09em] text-text-faint">
                  {selected.role}
                </span>
              </div>
              <p className="mt-1.5 max-w-[62ch] text-[12.5px] leading-relaxed text-text-muted">
                {selected.summary || "No description recorded for this node yet."}
              </p>
            </div>
            {selected.href && (
              <Link
                href={selected.href}
                className="flex-none rounded-[7px] border border-border-strong px-2.5 py-1.5 text-[12px] text-text-muted transition-colors hover:border-brand hover:text-brand"
              >
                Open live data →
              </Link>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
