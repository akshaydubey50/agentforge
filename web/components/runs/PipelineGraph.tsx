"use client";

import { useLayoutEffect, useRef, useState } from "react";
import type { SubtaskOut } from "@/lib/api";
import { catalogEntry } from "@/lib/toolCatalog";
import { cn } from "@/lib/utils";

interface Line {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  broken: boolean;
}

const STATUS_RING: Record<SubtaskOut["status"], string> = {
  done: "border-ok bg-ok-wash",
  running: "border-brand bg-brand-wash animate-now-pulse",
  needs_revision: "border-warn bg-warn-wash",
  escalated: "border-bad bg-bad-wash",
  failed: "border-bad bg-bad-wash",
  skipped: "border-border-strong bg-surface-2",
  pending: "border-border-strong bg-surface",
  ready: "border-border-strong bg-surface",
};

const STATUS_LABEL: Record<SubtaskOut["status"], string> = {
  done: "Done",
  running: "Running now",
  needs_revision: "Retrying",
  escalated: "Broke here",
  failed: "Broke here",
  skipped: "Skipped",
  pending: "Waiting",
  ready: "Waiting",
};

/** Longest-path-from-root depth -- plan_node only allows depends_on strictly
 * earlier positions (no cycles by construction), so this is always
 * well-defined without cycle detection. */
function computeDepths(subtasks: SubtaskOut[]): Map<string, number> {
  const byId = new Map(subtasks.map((s) => [s.id, s]));
  const depth = new Map<string, number>();
  function depthOf(id: string): number {
    if (depth.has(id)) return depth.get(id)!;
    const s = byId.get(id);
    if (!s || s.depends_on.length === 0) {
      depth.set(id, 0);
      return 0;
    }
    const d = 1 + Math.max(...s.depends_on.map(depthOf));
    depth.set(id, d);
    return d;
  }
  subtasks.forEach((s) => depthOf(s.id));
  return depth;
}

export function PipelineGraph({ subtasks }: { subtasks: SubtaskOut[] }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const nodeRefs = useRef<Map<string, HTMLDivElement>>(new Map());
  const [lines, setLines] = useState<Line[]>([]);

  const depths = computeDepths(subtasks);
  const stageCount = subtasks.length > 0 ? Math.max(...subtasks.map((s) => depths.get(s.id)!)) + 1 : 0;
  const stages: SubtaskOut[][] = Array.from({ length: stageCount }, () => []);
  for (const s of subtasks) stages[depths.get(s.id)!].push(s);
  stages.forEach((stage) => stage.sort((a, b) => a.position - b.position));

  useLayoutEffect(() => {
    function recompute() {
      const container = containerRef.current;
      if (!container) return;
      const containerRect = container.getBoundingClientRect();
      const next: Line[] = [];
      for (const s of subtasks) {
        const toEl = nodeRefs.current.get(s.id);
        if (!toEl) continue;
        const toRect = toEl.getBoundingClientRect();
        for (const depId of s.depends_on) {
          const fromEl = nodeRefs.current.get(depId);
          if (!fromEl) continue;
          const fromRect = fromEl.getBoundingClientRect();
          next.push({
            x1: fromRect.right - containerRect.left,
            y1: fromRect.top + fromRect.height / 2 - containerRect.top,
            x2: toRect.left - containerRect.left,
            y2: toRect.top + toRect.height / 2 - containerRect.top,
            broken: s.status === "escalated" || s.status === "failed",
          });
        }
      }
      setLines(next);
    }
    recompute();
    window.addEventListener("resize", recompute);
    return () => window.removeEventListener("resize", recompute);
  }, [subtasks]);

  if (subtasks.length === 0) return null;

  return (
    <div ref={containerRef} className="relative overflow-x-auto rounded-[var(--rm)] border border-border bg-surface p-5">
      <svg className="pointer-events-none absolute inset-0 h-full w-full" style={{ zIndex: 0 }}>
        {lines.map((line, i) => (
          <path
            key={i}
            d={`M ${line.x1} ${line.y1} C ${(line.x1 + line.x2) / 2} ${line.y1}, ${(line.x1 + line.x2) / 2} ${line.y2}, ${line.x2} ${line.y2}`}
            fill="none"
            stroke={line.broken ? "var(--bad)" : "var(--bd2)"}
            strokeWidth={line.broken ? 2 : 1.5}
          />
        ))}
      </svg>
      <div className="relative flex items-start gap-8" style={{ zIndex: 1 }}>
        {stages.map((stage, i) => (
          <div key={i} className="flex flex-none flex-col gap-4">
            <div className="text-[10px] font-extrabold uppercase tracking-wide text-text-faint">Stage {i + 1}</div>
            {stage.map((s) => {
              const tool = s.assigned_tool ? catalogEntry(s.assigned_tool, "") : null;
              return (
                <div
                  key={s.id}
                  ref={(el) => {
                    if (el) nodeRefs.current.set(s.id, el);
                  }}
                  className={cn(
                    "w-[190px] rounded-[var(--rm)] border-2 p-2.5 text-[12px] transition-colors",
                    STATUS_RING[s.status]
                  )}
                >
                  <div className="line-clamp-2 font-medium text-text">{s.description}</div>
                  <div className="mt-1.5 flex items-center justify-between text-[10.5px]">
                    <span className="text-text-faint">{tool ? tool.label : "reasoning"}</span>
                    <span
                      className={cn(
                        "font-bold",
                        s.status === "done" && "text-ok",
                        s.status === "running" && "text-brand",
                        (s.status === "escalated" || s.status === "failed") && "text-bad",
                        s.status === "needs_revision" && "text-warn"
                      )}
                    >
                      {STATUS_LABEL[s.status]}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        ))}
      </div>
    </div>
  );
}
