"use client";

import { useState } from "react";
import Link from "next/link";
import useSWR, { mutate } from "swr";
import { ArrowRight } from "lucide-react";
import { api, type EscalationDecision } from "@/lib/api";
import { ApprovalPanel } from "@/components/execution/ApprovalPanel";
import { EmptyState } from "@/components/ui/EmptyState";
import { SkeletonRows } from "@/components/ui/Skeleton";
import { Pagination } from "@/components/ui/Pagination";

const COUNT_KEY = "pending-escalations-count";

export default function ApprovalsPage() {
  const [offset, setOffset] = useState(0);
  const [limit, setLimit] = useState(25);
  const [busyId, setBusyId] = useState<string | null>(null);

  const { data, isLoading } = useSWR(
    ["pending-escalations", offset, limit],
    () => api.listEscalations("pending", limit, offset),
    { refreshInterval: 5000 }
  );
  const escalations = data?.items ?? [];
  const total = data?.total ?? 0;

  const decide = async (escalationId: string, decision: EscalationDecision) => {
    setBusyId(escalationId);
    try {
      await api.decideEscalation(escalationId, decision);
      mutate(["pending-escalations", offset, limit]);
      mutate(COUNT_KEY);
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className="flex-1 overflow-y-auto px-7 py-5.5">
      <div className="mx-auto max-w-[1040px]">
        <h1 className="mb-1.5 text-[17px] font-semibold text-text">Approvals</h1>
        <p className="mb-5.5 text-[12.5px] text-text-faint">
          Human-in-the-loop decisions. Each card shows the exact effect authorized; changed arguments require a new approval.
        </p>

        {isLoading && !data && <SkeletonRows />}
        {data && total === 0 && (
          <EmptyState glyph="OK" title="Nothing needs you" description="No pending approval gates are waiting for a human decision." />
        )}
        <div className="space-y-4">
          {escalations.map((escalation) => (
            <div key={escalation.id} className="rounded-[8px] border border-border bg-surface p-3">
              <div className="mb-3 flex items-center justify-between gap-3 px-1">
                <div className="font-mono text-[11px] text-text-faint">Run #{escalation.task_id.slice(0, 8)}</div>
                <Link href={`/workspace?run=${escalation.task_id}`} className="inline-flex items-center gap-1 text-[12px] text-role-supervisor hover:underline">
                  Open Workspace
                  <ArrowRight className="h-3.5 w-3.5" />
                </Link>
              </div>
              <ApprovalPanel escalation={escalation} deciding={busyId === escalation.id} onDecide={(decision) => decide(escalation.id, decision)} />
            </div>
          ))}
        </div>
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
