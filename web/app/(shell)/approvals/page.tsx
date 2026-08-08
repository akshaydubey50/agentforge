"use client";

import { useState } from "react";
import useSWR, { mutate } from "swr";
import { api } from "@/lib/api";
import { TopBar } from "@/components/shell/TopBar";
import { ApprovalCard } from "@/components/approvals/ApprovalCard";
import { EmptyState } from "@/components/ui/EmptyState";
import { SkeletonRows } from "@/components/ui/Skeleton";
import { Pagination } from "@/components/ui/Pagination";

const COUNT_KEY = "pending-escalations-count";

export default function ApprovalsPage() {
  const [offset, setOffset] = useState(0);
  const [limit, setLimit] = useState(25);

  const { data, isLoading } = useSWR(
    ["pending-escalations", offset, limit],
    () => api.listEscalations("pending", limit, offset),
    { refreshInterval: 5000 }
  );
  const escalations = data?.items ?? [];
  const total = data?.total ?? 0;

  const decide = async (
    escalationId: string,
    decision: "approve" | "reject" | "take_over",
    overrideOutput?: string
  ) => {
    await api.decideEscalation(escalationId, decision, { overrideOutput });
    mutate(["pending-escalations", offset, limit]);
    mutate(COUNT_KEY);
  };

  return (
    <>
      <TopBar title="Approvals" subtitle={data ? `${total} waiting on you` : undefined} />
      <div className="flex-1 overflow-y-auto px-5 py-5">
        {isLoading && !data && <SkeletonRows />}
        {data && total === 0 && (
          <EmptyState glyph="✓" title="Nothing needs you" description="Everything the assistant wanted to do has been decided." />
        )}
        {escalations.map((e) => (
          <ApprovalCard key={e.id} escalation={e} onDecide={(decision, output) => decide(e.id, decision, output)} />
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
    </>
  );
}
