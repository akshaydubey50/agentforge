"use client";

import { useState } from "react";
import useSWR from "swr";
import { api } from "@/lib/api";
import { MemoryCard } from "@/components/memory/MemoryCard";
import { SkeletonRows } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/ui/EmptyState";
import { Pagination } from "@/components/ui/Pagination";
import { cn } from "@/lib/utils";

const KINDS = ["all", "semantic", "episodic", "pinned_decision", "preference", "artifact_reference"];

export default function MemoryPage() {
  const [kind, setKind] = useState("all");
  const [offset, setOffset] = useState(0);
  const [limit, setLimit] = useState(25);

  const { data, isLoading } = useSWR(["memory-entries", kind, offset, limit], () => api.listMemory(kind, limit, offset));
  const entries = data?.items ?? [];
  const total = data?.total ?? 0;

  return (
    <div className="flex-1 overflow-y-auto px-7 py-5.5">
      <div className="mx-auto max-w-[1000px]">
        <h1 className="mb-1.5 text-[17px] font-semibold text-text">Long-term memory</h1>
        <p className="mb-5.5 text-[12.5px] text-text-faint">
          Read-only durable memory inspection. Conversation summaries, retrieved memory, and current context are separate surfaces; this page shows persisted memory entries exposed by the backend.
        </p>

        <div className="mb-4 flex flex-wrap gap-1.5">
          {KINDS.map((k) => (
            <button
              key={k}
              onClick={() => {
                setKind(k);
                setOffset(0);
              }}
              className={cn(
                "rounded-md border border-border px-3 py-1.5 text-[11.5px] text-text-muted",
                kind === k && "border-role-supervisor bg-surface-3 text-text"
              )}
            >
              {k}
            </button>
          ))}
        </div>

        {isLoading && !data && <SkeletonRows rows={4} />}
        {data && entries.length === 0 && (
          <EmptyState glyph="⬡" title="No memories yet" description="Episodic summaries are written when a task completes." />
        )}
        {entries.map((e) => (
          <MemoryCard key={e.id} entry={e} />
        ))}
        {total > 0 && (
          <Pagination
            offset={offset}
            limit={limit}
            total={total}
            onOffsetChange={setOffset}
            onLimitChange={(l) => {
              setLimit(l);
              setOffset(0);
            }}
          />
        )}
      </div>
    </div>
  );
}
