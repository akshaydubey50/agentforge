"use client";

import { useState } from "react";
import type { EscalationOut } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export function ApprovalCard({
  escalation,
  onDecide,
}: {
  escalation: EscalationOut;
  onDecide: (decision: "approve" | "reject" | "take_over", overrideOutput?: string) => Promise<void>;
}) {
  const [takingOver, setTakingOver] = useState(false);
  const [overrideOutput, setOverrideOutput] = useState("");
  const [busy, setBusy] = useState(false);
  const [resolved, setResolved] = useState(false);

  const decide = async (decision: "approve" | "reject" | "take_over", output?: string) => {
    setBusy(true);
    try {
      await onDecide(decision, output);
      setResolved(true);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className={cn(
        "mb-2.5 overflow-hidden rounded-[var(--rm)] border border-border border-l-[3px] border-l-warn bg-surface px-4 py-3.5 transition-all duration-300",
        resolved && "m-0 max-h-0 border-0 p-0 opacity-0"
      )}
      style={{ maxHeight: resolved ? 0 : 260 }}
    >
      <div className="mb-2 text-[14px] font-semibold text-text">Needs your decision</div>
      <div className="mb-1 grid grid-cols-[52px_1fr] gap-x-3 gap-y-0.5 text-[12.5px]">
        <b className="text-[10.5px] font-extrabold uppercase tracking-wide text-text-faint">Why</b>
        <span className="text-text">{escalation.reason}</span>
      </div>
      <div className="mb-1 grid grid-cols-[52px_1fr] gap-x-3 gap-y-0.5 text-[12.5px]">
        <b className="text-[10.5px] font-extrabold uppercase tracking-wide text-text-faint">Run</b>
        <a href={`/runs/${escalation.task_id}`} className="mono text-[11.5px] text-brand underline">
          {escalation.task_id.slice(0, 8)}
        </a>
      </div>

      {!takingOver ? (
        <div className="mt-3 flex flex-wrap gap-2">
          <Button size="sm" disabled={busy} onClick={() => decide("approve")}>
            Approve
          </Button>
          <Button variant="outline" size="sm" disabled={busy} onClick={() => decide("reject")}>
            Reject
          </Button>
          <Button variant="outline" size="sm" disabled={busy} onClick={() => setTakingOver(true)}>
            I&apos;ll provide the answer
          </Button>
        </div>
      ) : (
        <div className="mt-3">
          <textarea
            className="w-full rounded-[var(--rs)] border border-border-strong bg-background p-2.5 text-[13px] text-text"
            rows={3}
            placeholder="What should the answer for this step be?"
            value={overrideOutput}
            onChange={(e) => setOverrideOutput(e.target.value)}
          />
          <div className="mt-2 flex gap-2">
            <Button size="sm" disabled={busy || !overrideOutput.trim()} onClick={() => decide("take_over", overrideOutput.trim())}>
              Submit &amp; resume
            </Button>
            <Button variant="outline" size="sm" disabled={busy} onClick={() => setTakingOver(false)}>
              Cancel
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
