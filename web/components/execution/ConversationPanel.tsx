"use client";

import { FormEvent, useState } from "react";
import type { ComponentType } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Database,
  FileText,
  MessageSquare,
  ShieldAlert,
  Sparkles,
  User,
  Wrench,
} from "lucide-react";
import type { RuntimeCard } from "@/lib/execution/types";
import { cn } from "@/lib/utils";
import { statusLabel } from "./ExecutionNodeView";

const CARD_META: Record<RuntimeCard["kind"], { icon: ComponentType<{ className?: string }>; label: string; className: string }> = {
  user: { icon: User, label: "User", className: "border-border bg-surface" },
  assistant: { icon: Sparkles, label: "Agent", className: "border-role-supervisor/35 bg-role-supervisor/5" },
  plan: { icon: MessageSquare, label: "Plan", className: "border-role-supervisor/35 bg-role-supervisor/5" },
  approval: { icon: ShieldAlert, label: "Approval", className: "border-role-human/45 bg-role-human/7" },
  tool: { icon: Wrench, label: "Tool", className: "border-status-running/35 bg-status-running/5" },
  verification: { icon: CheckCircle2, label: "Verification", className: "border-status-completed/35 bg-status-completed/5" },
  memory: { icon: Database, label: "Memory", className: "border-ok/35 bg-ok/5" },
  artifact: { icon: FileText, label: "Artifact", className: "border-border bg-surface-2" },
  error: { icon: AlertTriangle, label: "Error", className: "border-status-failed/45 bg-status-failed/7" },
  system: { icon: Sparkles, label: "Runtime", className: "border-border bg-surface-2" },
};

export function ConversationPanel({
  cards,
  selectedNodeId,
  canSend,
  sending,
  onSelectNode,
  onSend,
}: {
  cards: RuntimeCard[];
  selectedNodeId: string | null;
  canSend: boolean;
  sending: boolean;
  onSelectNode: (nodeId: string | null) => void;
  onSend: (content: string) => Promise<void>;
}) {
  const [content, setContent] = useState("");

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const value = content.trim();
    if (!value || !canSend || sending) return;
    setContent("");
    await onSend(value);
  };

  return (
    <section className="flex min-h-0 flex-1 flex-col bg-rail">
      <div className="border-b border-border px-4 py-3">
        <div className="text-[13px] font-semibold text-text">Conversation</div>
        <div className="mt-0.5 text-[11.5px] text-text-faint">Readable goal, answer, and high-signal runtime cards.</div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
        <div className="space-y-3">
          {cards.map((card) => {
            const meta = CARD_META[card.kind];
            const Icon = meta.icon;
            const active = card.nodeId && card.nodeId === selectedNodeId;
            return (
              <button
                key={card.id}
                type="button"
                onClick={() => onSelectNode(card.nodeId ?? null)}
                className={cn(
                  "w-full rounded-[8px] border px-3.5 py-3 text-left transition hover:border-border-strong",
                  meta.className,
                  active && "ring-2 ring-role-supervisor/80"
                )}
              >
                <div className="mb-2 flex items-center gap-2">
                  <Icon className="h-3.5 w-3.5 text-text-muted" />
                  <span className="text-[10px] uppercase tracking-[0.1em] text-text-faint">{meta.label}</span>
                  {card.status && (
                    <span className="ml-auto rounded-full bg-background/70 px-1.5 py-0.5 text-[10px] text-text-faint">
                      {statusLabel(card.status)}
                    </span>
                  )}
                </div>
                <div className="text-[13px] font-semibold text-text">{card.title}</div>
                <div className="mt-1 whitespace-pre-wrap text-[12px] leading-relaxed text-text-muted">{card.body}</div>
              </button>
            );
          })}
          {cards.length === 0 && <div className="py-8 text-center text-[12px] text-text-faint">No run conversation loaded.</div>}
        </div>
      </div>

      <form onSubmit={submit} className="border-t border-border p-3">
        <textarea
          value={content}
          onChange={(event) => setContent(event.target.value)}
          disabled={!canSend || sending}
          placeholder={canSend ? "Continue this run..." : "Follow-up is available after the run stops."}
          className="min-h-[84px] w-full resize-none rounded-[8px] border border-border bg-background px-3 py-2 text-[12.5px] text-text outline-none transition focus:border-role-supervisor disabled:cursor-not-allowed disabled:opacity-55"
        />
        <div className="mt-2 flex items-center justify-between gap-2">
          <span className="text-[11px] text-text-faint">Follow-ups attach to this same run identity.</span>
          <button
            type="submit"
            disabled={!content.trim() || !canSend || sending}
            className="rounded-[7px] bg-role-supervisor px-3 py-1.5 text-[12px] font-semibold text-background transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-45"
          >
            {sending ? "Sending" : "Send"}
          </button>
        </div>
      </form>
    </section>
  );
}
