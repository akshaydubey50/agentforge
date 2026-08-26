"use client";

import { useState } from "react";
import type { TaskStatus } from "@/lib/api";
import { canContinueConversation } from "@/lib/agentStatus";
import { Button } from "@/components/ui/button";

function disabledReason(status: TaskStatus): string {
  switch (status) {
    case "awaiting_approval":
      return "Resolve the pending escalation above before continuing the conversation.";
    case "running":
    case "pending":
      return "The task is still working — you can continue the conversation once it finishes.";
    default:
      return "";
  }
}

export function FeedComposer({ status, onSend }: { status: TaskStatus; onSend: (content: string) => Promise<void> }) {
  const [value, setValue] = useState("");
  const [sending, setSending] = useState(false);
  const enabled = canContinueConversation(status);

  const submit = async () => {
    const content = value.trim();
    if (!content || sending || !enabled) return;
    setSending(true);
    try {
      await onSend(content);
      setValue("");
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="flex-none border-t border-border px-5.5 py-3.5">
      {enabled ? (
        <div className="flex gap-2">
          <input
            className="flex-1 rounded-[10px] border border-border bg-surface px-3.5 py-2.5 text-[13px] text-text placeholder:text-text-faint focus-visible:border-role-supervisor"
            placeholder="Continue the conversation — ask a follow-up…"
            value={value}
            disabled={sending}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") submit();
            }}
          />
          <Button size="sm" className="bg-role-supervisor text-background hover:bg-role-supervisor/90" disabled={sending || !value.trim()} onClick={submit}>
            {sending ? "Sending…" : "Send"}
          </Button>
        </div>
      ) : (
        <div className="rounded-[10px] border border-border bg-surface px-3.5 py-2.5 text-[13px] text-text-faint">
          {disabledReason(status)}
        </div>
      )}
    </div>
  );
}
