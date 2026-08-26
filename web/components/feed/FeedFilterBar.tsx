"use client";

import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";

export interface RoleFilter {
  specialist: boolean;
  reviewer: boolean;
}

const DEFAULT_FILTER: RoleFilter = { specialist: true, reviewer: true };
const STORAGE_KEY = "agentforge:feed-role-filter";

export function useRoleFilter(): [RoleFilter, (next: RoleFilter) => void] {
  const [filter, setFilter] = useState<RoleFilter>(DEFAULT_FILTER);

  useEffect(() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      if (stored) setFilter({ ...DEFAULT_FILTER, ...JSON.parse(stored) });
    } catch {
      // localStorage unavailable or bad JSON -- just keep the default filter
    }
  }, []);

  const update = (next: RoleFilter) => {
    setFilter(next);
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    } catch {
      // best-effort persistence only
    }
  };

  return [filter, update];
}

export function applyRoleFilter<T extends { kind: string }>(items: T[], filter: RoleFilter): T[] {
  return items.filter((item) => {
    if (!filter.specialist && (item.kind === "tool_call" || item.kind === "reasoning")) return false;
    if (!filter.reviewer && item.kind === "review") return false;
    return true;
  });
}

function FilterChip({ label, active, onClick }: { label: string; active: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "rounded-md border px-2.5 py-1 text-[11px] transition-colors",
        active ? "border-role-supervisor bg-surface-3 text-text" : "border-border text-text-faint hover:text-text-muted"
      )}
    >
      {active ? "✓ " : ""}
      {label}
    </button>
  );
}

export function FeedFilterBar({ filter, onChange }: { filter: RoleFilter; onChange: (next: RoleFilter) => void }) {
  return (
    <div className="mb-3.5 flex items-center gap-1.5">
      <span className="text-[10.5px] uppercase tracking-wide text-text-faint">Show</span>
      <FilterChip label="Specialist" active={filter.specialist} onClick={() => onChange({ ...filter, specialist: !filter.specialist })} />
      <FilterChip label="Reviewer" active={filter.reviewer} onClick={() => onChange({ ...filter, reviewer: !filter.reviewer })} />
    </div>
  );
}
