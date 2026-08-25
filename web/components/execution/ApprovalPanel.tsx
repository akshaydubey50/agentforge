"use client";

import { AlertTriangle, ShieldCheck, UserCheck, XCircle } from "lucide-react";
import type { ReactNode } from "react";
import type { EscalationDecision, EscalationOut } from "@/lib/api";
import { humanizeValue } from "@/lib/formatValue";
import { cn } from "@/lib/utils";

function pick(context: Record<string, unknown>, keys: string[]): unknown {
  for (const key of keys) {
    if (context[key] !== undefined && context[key] !== null && context[key] !== "") return context[key];
  }
  return null;
}

function text(value: unknown, fallback = "Not provided"): string {
  return typeof value === "string" && value.trim() ? value.trim() : fallback;
}

function fieldFromArgs(args: unknown, keys: string[]): unknown {
  if (!args || typeof args !== "object") return null;
  const record = args as Record<string, unknown>;
  for (const key of keys) {
    if (record[key] !== undefined && record[key] !== null && record[key] !== "") return record[key];
  }
  return null;
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[92px_minmax(0,1fr)] gap-3 border-t border-border py-2 first:border-t-0">
      <div className="text-[11px] uppercase tracking-[0.08em] text-text-faint">{label}</div>
      <div className="min-w-0 text-[12.5px] text-text">{children}</div>
    </div>
  );
}

export function ApprovalPanel({
  escalation,
  deciding,
  compact,
  onDecide,
}: {
  escalation: EscalationOut;
  deciding: boolean;
  compact?: boolean;
  onDecide: (decision: EscalationDecision) => Promise<void>;
}) {
  const context = escalation.context ?? {};
  const policy = pick(context, ["policy", "policy_decision"]);
  const args = pick(context, ["validated_args", "arguments", "args", "tool_args"]);
  const toolName = text(pick(context, ["tool_name", "tool", "name"]), escalation.kind);
  const fingerprint = pick(context, ["approval_fingerprint", "fingerprint", "effect_fingerprint"]);
  const expiresAt = pick(context, ["expires_at", "approval_expires_at", "expiry"]);
  const argsChanged = Boolean(pick(context, ["args_changed", "fingerprint_mismatch", "new_approval_required"]));
  const risk = pick(context, ["risk", "risk_level", "action_type"]);
  const bodyPreview = pick(context, ["body_preview", "preview", "body"]);
  const recipient = pick(context, ["to", "recipient", "email"]) ?? fieldFromArgs(args, ["to", "recipient", "email"]);
  const subject = pick(context, ["subject"]) ?? fieldFromArgs(args, ["subject"]);

  return (
    <section
      className={cn(
        "rounded-[8px] border border-role-human/55 bg-role-human/8 text-text shadow-sm",
        compact ? "p-3" : "p-4"
      )}
      aria-live="polite"
    >
      <div className="mb-3 flex items-start gap-3">
        <div className="mt-0.5 flex h-8 w-8 flex-none items-center justify-center rounded-[7px] bg-role-human/15 text-role-human">
          <UserCheck className="h-4 w-4" />
        </div>
        <div className="min-w-0">
          <div className="text-[13.5px] font-semibold text-text">Approval required</div>
          <div className="mt-0.5 text-[12px] text-text-muted">{escalation.reason}</div>
        </div>
      </div>

      {argsChanged && (
        <div className="mb-3 flex items-center gap-2 rounded-[7px] border border-status-failed/40 bg-status-failed/8 px-2.5 py-2 text-[12px] text-status-failed">
          <AlertTriangle className="h-3.5 w-3.5" />
          Arguments changed. New approval is required.
        </div>
      )}

      <div className="rounded-[7px] border border-border bg-background/55 px-3 py-1">
        <Field label="Tool">{toolName}</Field>
        {recipient !== null && recipient !== undefined && <Field label="To">{text(recipient)}</Field>}
        {subject !== null && subject !== undefined && <Field label="Subject">{text(subject)}</Field>}
        <Field label="Effect">
          <pre className="max-h-32 overflow-auto whitespace-pre-wrap font-sans text-[12px] leading-relaxed text-text-muted">
            {args ? humanizeValue(args) : "Validated arguments were not exposed by this approval context."}
          </pre>
        </Field>
        {bodyPreview !== null && bodyPreview !== undefined && (
          <Field label="Preview">
            <div className="line-clamp-5 whitespace-pre-wrap text-[12px] leading-relaxed text-text-muted">{text(bodyPreview)}</div>
          </Field>
        )}
        <Field label="Risk">{risk ? humanizeValue(risk) : "Policy risk metadata unavailable"}</Field>
        <Field label="Policy">{policy ? humanizeValue(policy) : "Runtime policy requires a human decision"}</Field>
        <Field label="Fingerprint">{fingerprint ? String(fingerprint) : "Not exposed by current backend context"}</Field>
        <Field label="Expires">{expiresAt ? String(expiresAt) : "No expiry timestamp exposed"}</Field>
      </div>

      {escalation.status === "pending" ? (
        <div className="mt-3 flex gap-2">
          <button
            type="button"
            disabled={deciding || argsChanged}
            onClick={() => onDecide("approve")}
            className="inline-flex flex-1 items-center justify-center gap-1.5 rounded-[7px] bg-status-completed px-3 py-2 text-[12px] font-semibold text-background transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-45"
          >
            <ShieldCheck className="h-3.5 w-3.5" />
            Approve
          </button>
          <button
            type="button"
            disabled={deciding}
            onClick={() => onDecide("reject")}
            className="inline-flex flex-1 items-center justify-center gap-1.5 rounded-[7px] border border-status-failed/55 px-3 py-2 text-[12px] font-semibold text-status-failed transition hover:bg-status-failed/8 disabled:cursor-not-allowed disabled:opacity-45"
          >
            <XCircle className="h-3.5 w-3.5" />
            Reject
          </button>
        </div>
      ) : (
        <div className="mt-3 rounded-[7px] border border-border bg-background/50 px-3 py-2 text-[12px] text-text-muted">
          Decision recorded: <span className="font-semibold text-text">{escalation.status}</span>
          {escalation.decision_note ? ` (${escalation.decision_note})` : ""}
        </div>
      )}
    </section>
  );
}
