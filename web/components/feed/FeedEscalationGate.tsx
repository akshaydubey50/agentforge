"use client";

import { useState } from "react";
import type { EscalationOut } from "@/lib/api";
import { classifyEscalation } from "@/lib/escalationTag";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export function FeedEscalationGate({
  escalation,
  onDecide,
}: {
  escalation: EscalationOut;
  onDecide: (decision: "approve" | "reject" | "take_over", overrideOutput?: string) => Promise<void>;
}) {
  const [takingOver, setTakingOver] = useState(false);
  const [overrideOutput, setOverrideOutput] = useState("");
  const [busy, setBusy] = useState(false);
  const { tag } = classifyEscalation(escalation.reason, escalation.subtask_id);

  const decide = async (decision: "approve" | "reject" | "take_over", output?: string) => {
    setBusy(true);
    try {
      await onDecide(decision, output);
    } finally {
      setBusy(false);
    }
  };

  if (escalation.status !== "pending") {
    return (
      <div className="ml-11 rounded-[13px] border border-border bg-surface px-4.5 py-3.5">
        <div className="mb-1.5 text-[13px] font-semibold text-text-muted">
          {escalation.status === "approved" && "✓ Approved"}
          {escalation.status === "rejected" && "✕ Rejected"}
          {escalation.status === "took_over" && "↦ Taken over"}
        </div>
        <div className="mono rounded-md bg-canvas px-3 py-2 text-[11.5px] leading-relaxed text-text-muted">
          {escalation.reason}
        </div>
        {escalation.decision_note && <div className="mt-1.5 text-[12px] text-text-muted">{escalation.decision_note}</div>}
      </div>
    );
  }

  return (
    <div className="ml-11 rounded-[13px] border border-role-human bg-gradient-to-br from-role-human/10 to-role-human/[0.02] px-4.5 py-4">
      <div className="mb-1.5 text-[13.5px] font-semibold text-role-human">⏸ Escalation — awaiting your decision</div>
      <div className="mono mb-3 rounded-md bg-canvas px-3 py-2 text-[12px] leading-relaxed text-text-muted">
        <span className="mr-2 rounded border border-border-strong px-1.5 py-0.5 text-[10px] text-text-faint">{tag}</span>
        {escalation.reason}
      </div>

      {!takingOver ? (
        <div className="flex flex-wrap gap-2">
          <Button size="sm" disabled={busy} className="bg-status-completed text-badT hover:bg-status-completed/90" onClick={() => decide("approve")}>
            Approve
          </Button>
          <Button size="sm" disabled={busy} className="bg-role-human text-background hover:bg-role-human/90" onClick={() => setTakingOver(true)}>
            Take over
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={busy}
            className="border-status-failed text-status-failed hover:bg-status-failed/10"
            onClick={() => decide("reject")}
          >
            Reject
          </Button>
        </div>
      ) : (
        <div>
          <textarea
            className={cn(
              "w-full rounded-md border border-dashed border-border-strong bg-canvas px-3 py-2.5 text-[12.5px] text-text placeholder:text-text-faint"
            )}
            rows={3}
            placeholder="Type the exact output the agent should use, or leave blank to approve its version…"
            value={overrideOutput}
            onChange={(e) => setOverrideOutput(e.target.value)}
          />
          <div className="mt-2 flex gap-2">
            <Button size="sm" disabled={busy || !overrideOutput.trim()} className="bg-role-human text-background hover:bg-role-human/90" onClick={() => decide("take_over", overrideOutput.trim())}>
              Submit &amp; resume
            </Button>
            <Button size="sm" variant="outline" disabled={busy} onClick={() => setTakingOver(false)}>
              Cancel
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
