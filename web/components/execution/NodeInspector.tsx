"use client";

import { Braces, Clock3, Info, X } from "lucide-react";
import type { ReactNode } from "react";
import type { EscalationDecision, EscalationOut } from "@/lib/api";
import { humanizeValue } from "@/lib/formatValue";
import type { ExecutionNode, RunModel } from "@/lib/execution/types";
import { cn } from "@/lib/utils";
import { actorLabel, statusLabel } from "./ExecutionNodeView";
import { ApprovalPanel } from "./ApprovalPanel";
import { ContextInspector } from "./ContextInspector";

function Section({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="border-b border-border px-4 py-3.5 last:border-b-0">
      <div className="mb-2 text-[10.5px] uppercase tracking-[0.1em] text-text-faint">{label}</div>
      {children}
    </div>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="mb-2 flex justify-between gap-3 text-[12px] last:mb-0">
      <span className="flex-none text-text-muted">{label}</span>
      <span className="min-w-0 truncate font-mono text-text">{children}</span>
    </div>
  );
}

function SafeBlock({ value, empty = "No exposed payload." }: { value: unknown; empty?: string }) {
  const content = value === undefined || value === null || value === "" ? empty : humanizeValue(value);
  return (
    <pre className="max-h-[220px] overflow-auto whitespace-pre-wrap rounded-[7px] border border-border bg-background p-2.5 font-sans text-[12px] leading-relaxed text-text-muted">
      {content}
    </pre>
  );
}

function selectedEscalation(model: RunModel, node: ExecutionNode | null): EscalationOut | null {
  if (node?.escalationId) {
    return model.pendingApproval?.id === node.escalationId
      ? model.pendingApproval
      : model.events.find((event) => event.escalation?.id === node.escalationId)?.escalation ?? null;
  }
  return model.pendingApproval;
}

export function NodeInspector({
  model,
  selectedNode,
  open,
  deciding,
  onClose,
  onDecide,
}: {
  model: RunModel;
  selectedNode: ExecutionNode | null;
  open: boolean;
  deciding: boolean;
  onClose: () => void;
  onDecide: (escalationId: string, decision: EscalationDecision) => Promise<void>;
}) {
  const escalation = selectedEscalation(model, selectedNode);
  const node = selectedNode ?? model.nodes.find((item) => item.id === model.currentNodeId) ?? null;

  return (
    <aside
      className={cn(
        "flex min-h-0 flex-col border-l border-border bg-rail transition-[width]",
        open ? "w-full min-w-0" : "w-0 overflow-hidden border-l-0"
      )}
    >
      <div className="flex h-[52px] flex-none items-center gap-2 border-b border-border px-4">
        <Info className="h-4 w-4 text-text-muted" />
        <div className="min-w-0 flex-1">
          <div className="text-[13px] font-semibold text-text">Inspector</div>
          <div className="truncate text-[11px] text-text-faint">Context-sensitive runtime details</div>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Collapse inspector"
          className="rounded-[6px] p-1 text-text-faint transition hover:bg-surface-3 hover:text-text"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {!node && (
          <div className="px-4 py-6 text-[12.5px] leading-relaxed text-text-muted">
            Select a graph node or timeline event to inspect exposed inputs, outputs, policy decisions, context metrics, and verification metadata.
          </div>
        )}

        {node && (
          <>
            <Section label="Overview">
              <div className="mb-3 text-[15px] font-semibold text-text">{node.label}</div>
              <Row label="Status">{statusLabel(node.status)}</Row>
              <Row label="Family">{node.family}</Row>
              <Row label="Actor">{actorLabel(node)}</Row>
              <Row label="Type">{node.type}</Row>
              {typeof node.metadata.spanCount === "number" && <Row label="Trace events">{String(node.metadata.spanCount)}</Row>}
              {typeof node.metadata.subtaskCount === "number" && <Row label="Subtasks">{String(node.metadata.subtaskCount)}</Row>}
              {typeof node.metadata.liveEventCount === "number" && <Row label="Live events">{String(node.metadata.liveEventCount)}</Row>}
              {node.toolName && <Row label="Tool">{node.toolName}</Row>}
              {node.startedAt && <Row label="Started">{new Date(node.startedAt).toLocaleString()}</Row>}
              {node.completedAt && <Row label="Ended">{new Date(node.completedAt).toLocaleString()}</Row>}
            </Section>

            {(node.family === "reasoning" || node.type === "plan" || node.type === "synthesis") && (
              <>
                <Section label="Model output">
                  <SafeBlock value={node.metadata.output ?? node.metadata.subtask ?? node.metadata.finalOutput} empty="No user-visible model output is exposed for this node." />
                </Section>
                <Section label="Context">
                  <ContextInspector context={model.context} />
                </Section>
                <Section label="Metrics">
                  <Row label="Duration">{String(node.metadata.duration ?? "Not exposed")}</Row>
                  <Row label="Trace spans">{node.traceSpanIds.length}</Row>
                  <Row label="Tokens">Use Context tab when available</Row>
                </Section>
              </>
            )}

            {node.family === "action" && (
              <>
                <Section label="Validated args">
                  <SafeBlock value={node.metadata.input ?? node.metadata.subtask} empty="Validated arguments are not exposed for this node." />
                </Section>
                <Section label="Result">
                  <SafeBlock value={node.metadata.output} empty="No result metadata recorded yet." />
                </Section>
                <Section label="Execution">
                  <Row label="Safety">{String(node.metadata.execution_safety ?? node.metadata.safety ?? "Not exposed")}</Row>
                  <Row label="Effect key">{String(node.metadata.effect_key ?? node.metadata.effectKey ?? "Not exposed")}</Row>
                  <Row label="Retries">{String(node.metadata.retries ?? node.metadata.attempt_count ?? "See subtask attempt count")}</Row>
                </Section>
              </>
            )}

            {node.family === "control" && node.type === "policy" && (
              <Section label="Decision">
                <SafeBlock value={node.metadata.policy ?? node.metadata.output} empty="Policy metadata is not exposed." />
              </Section>
            )}

            {node.family === "control" && node.type === "approval" && escalation && (
              <Section label="Exact effect">
                <ApprovalPanel
                  escalation={escalation}
                  deciding={deciding}
                  compact
                  onDecide={(decision) => onDecide(escalation.id, decision)}
                />
              </Section>
            )}

            {node.family === "context" && (
              <>
                <Section label={node.type === "knowledge" ? "Retrieved evidence" : "Memory"}>
                  <SafeBlock value={node.metadata.output ?? node.metadata.input} empty="Context selection metadata is not exposed for this node." />
                </Section>
                <Section label="Provenance">
                  <SafeBlock value={node.metadata.source ?? node.metadata.provenance ?? node.metadata} />
                </Section>
              </>
            )}

            {node.family === "quality" && (
              <>
                <Section label="Expected vs observed">
                  <SafeBlock value={node.metadata.output ?? node.metadata.input} empty="Verification result is not exposed for this node." />
                </Section>
                <Section label="Outcome">
                  <Row label="Result">{statusLabel(node.status)}</Row>
                  <Row label="Safety">{String(node.metadata.safety ?? "Not exposed")}</Row>
                </Section>
              </>
            )}

            {node.family === "system" && (
              <Section label="Runtime metadata">
                <SafeBlock value={node.metadata} empty="No runtime metadata exposed." />
              </Section>
            )}

            <Section label="Raw trace boundary">
              <div className="flex items-start gap-2 text-[11.5px] leading-relaxed text-text-muted">
                <Braces className="mt-0.5 h-3.5 w-3.5 flex-none" />
                This inspector renders exposed structured output, policy, tool, memory, evidence, and metrics. It does not expose hidden chain-of-thought.
              </div>
            </Section>
          </>
        )}

        {!selectedNode && escalation && escalation.status === "pending" && (
          <Section label="Current approval">
            <ApprovalPanel escalation={escalation} deciding={deciding} compact onDecide={(decision) => onDecide(escalation.id, decision)} />
          </Section>
        )}
      </div>
    </aside>
  );
}
