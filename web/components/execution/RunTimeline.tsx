"use client";

import { ChevronDown, ChevronUp, Clock3 } from "lucide-react";
import type { RunEvent } from "@/lib/execution/types";
import { cn } from "@/lib/utils";
import { statusLabel } from "./ExecutionNodeView";

function elapsedLabel(startIso: string, eventIso: string): string {
  const ms = Math.max(0, new Date(eventIso).getTime() - new Date(startIso).getTime());
  return `${(ms / 1000).toFixed(1)}s`;
}

export function RunTimeline({
  events,
  runStartedAt,
  selectedEventId,
  selectedNodeId,
  collapsed,
  onSelectEvent,
  onSelectNode,
  onToggle,
}: {
  events: RunEvent[];
  runStartedAt: string;
  selectedEventId: string | null;
  selectedNodeId: string | null;
  collapsed: boolean;
  onSelectEvent: (eventId: string | null) => void;
  onSelectNode: (nodeId: string | null) => void;
  onToggle: () => void;
}) {
  const selectedEvent = events.find((event) => event.id === selectedEventId || Boolean(event.nodeId && event.nodeId === selectedNodeId));

  return (
    <section className="h-full min-w-0 overflow-hidden border-t border-border bg-rail">
      <button
        type="button"
        onClick={onToggle}
        className={cn(
          "flex h-[38px] w-full items-center gap-2 px-4 text-left transition hover:bg-surface-2",
          !collapsed && "border-b border-border"
        )}
        aria-expanded={!collapsed}
        aria-label={collapsed ? "Expand timeline" : "Collapse timeline"}
      >
        <Clock3 className="h-3.5 w-3.5 text-text-muted" />
        <div className="text-[12px] font-semibold text-text">Timeline</div>
        <div className="truncate text-[11px] text-text-faint">
          {selectedEvent ? selectedEvent.label : `${events.length} events`}
        </div>
        <div className="ml-auto text-[11px] uppercase tracking-[0.08em] text-text-faint">{events.length}</div>
        {collapsed ? <ChevronUp className="h-3.5 w-3.5 text-text-muted" /> : <ChevronDown className="h-3.5 w-3.5 text-text-muted" />}
      </button>
      {!collapsed && (
      <div className="rail-scrollbar flex h-[calc(100%-38px)] min-w-0 gap-2 overflow-x-auto px-4 py-3">
        {events.map((event) => {
          const active = event.id === selectedEventId || Boolean(event.nodeId && event.nodeId === selectedNodeId);
          return (
            <button
              key={event.id}
              type="button"
              onClick={() => {
                onSelectEvent(event.id);
                if (event.nodeId) onSelectNode(event.nodeId);
              }}
              className={cn(
                "flex w-[210px] flex-none flex-col rounded-[7px] border bg-background px-3 py-2 text-left transition hover:border-border-strong",
                active ? "border-role-supervisor ring-1 ring-role-supervisor/50" : "border-border"
              )}
            >
              <span className="font-mono text-[10px] text-text-faint">{elapsedLabel(runStartedAt, event.timestamp)}</span>
              <span className="mt-1 truncate text-[12.5px] font-semibold text-text">{event.label}</span>
              <span className="mt-0.5 line-clamp-2 text-[11px] leading-snug text-text-muted">{event.detail ?? event.kind}</span>
              {event.status && <span className="mt-auto pt-1.5 text-[10px] uppercase tracking-[0.08em] text-text-faint">{statusLabel(event.status)}</span>}
            </button>
          );
        })}
        {events.length === 0 && <div className="py-4 text-[12px] text-text-faint">No timeline events available.</div>}
      </div>
      )}
    </section>
  );
}
