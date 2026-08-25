import { humanizeValue } from "@/lib/formatValue";
import type { GraphNodeData } from "@/lib/agentGraph";
import type { TaskDetailOut } from "@/lib/api";
import { ROLE_META } from "@/lib/agentRoles";
import { SUBTASK_STATUS_META } from "@/lib/agentStatus";
import { cn } from "@/lib/utils";

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="border-b border-border px-4.5 py-3.5">
      <div className="mb-2 text-[10.5px] uppercase tracking-wide text-text-faint">{label}</div>
      {children}
    </div>
  );
}

function Row({ k, children }: { k: string; children: React.ReactNode }) {
  return (
    <div className="mb-2 flex justify-between gap-3 text-[12px] last:mb-0">
      <span className="flex-none text-text-muted">{k}</span>
      <span className="mono truncate text-text">{children}</span>
    </div>
  );
}

export function GraphInspector({ node, task }: { node: GraphNodeData | null; task: TaskDetailOut }) {
  if (!node) {
    return (
      <div className="flex w-[310px] flex-none flex-col overflow-y-auto border-l border-border bg-rail">
        <div className="border-b border-border px-4.5 py-3.5 text-[11px] uppercase tracking-wide text-text-faint">Node inspector</div>
        <div className="px-4.5 py-6 text-[12.5px] text-text-faint">Click a node to inspect its state and trace spans.</div>
        <Legend />
      </div>
    );
  }

  const meta = ROLE_META[node.role];

  return (
    <div className="flex w-[310px] flex-none flex-col overflow-y-auto border-l border-border bg-rail">
      <div className="border-b border-border px-4.5 py-3.5 text-[11px] uppercase tracking-wide text-text-faint">Node inspector</div>

      <div className="border-b border-border px-4.5 py-3.5">
        <div className="mb-1 flex items-center gap-2 text-[14px] font-semibold text-text">
          <span className={cn("flex h-[22px] w-[22px] items-center justify-center rounded-md text-[9px] font-bold", meta.bgClass, meta.colorClass)}>
            {meta.initial}
          </span>
          {node.title}
        </div>
        <div className="mono text-[11.5px] text-text-faint">{node.lane}</div>
      </div>

      {node.kind === "execute" && node.subtask && (
        <Section label="State">
          <Row k="status">{SUBTASK_STATUS_META[node.subtask.status].label}</Row>
          <Row k="attempt">{node.subtask.attempt_count}</Row>
          <Row k="depends on">
            {node.subtask.position === 0 ? "nothing (first step)" : `#${node.subtask.position - 1} (sequential)`}
          </Row>
        </Section>
      )}

      {node.kind === "review" && node.spans[0] && (
        <Section label="Verdict">
          {(() => {
            const out = node.spans[0].output as { score?: number; verdict?: string; feedback?: string };
            return (
              <>
                <Row k="verdict">{out.verdict}</Row>
                <Row k="score">{out.score}/5</Row>
                <div className="mt-2 text-[12px] leading-relaxed text-text-muted">{out.feedback}</div>
              </>
            );
          })()}
        </Section>
      )}

      {node.kind === "sketch" && node.spans[0] && (
        <Section label="Sketch">
          {(() => {
            const out = node.spans[0].output as { outline?: string[]; confidence?: number; reasoning?: string };
            return (
              <>
                <Row k="confidence">{out.confidence}/5</Row>
                <div className="mt-2 text-[12px] leading-relaxed text-text-muted">{out.reasoning}</div>
                {out.outline && out.outline.length > 0 && (
                  <ul className="mt-2 space-y-1 text-[11.5px] text-text-muted">
                    {out.outline.map((step, i) => (
                      <li key={i}>· {step}</li>
                    ))}
                  </ul>
                )}
              </>
            );
          })()}
        </Section>
      )}

      {node.kind === "escalation" && node.escalation && (
        <Section label="Escalation">
          <Row k="status">{node.escalation.status}</Row>
          <div className="mt-2 text-[12px] leading-relaxed text-text-muted">{node.escalation.reason}</div>
          {node.escalation.decision_note && <div className="mt-2 text-[12px] text-text-muted">note: {node.escalation.decision_note}</div>}
        </Section>
      )}

      {node.kind === "synthesize" &&
        (() => {
          // node.spans holds this specific turn's synthesize span (see
          // buildGraph) -- fall back to task.final_output only for a
          // single-turn task where that's the same thing, so a multi-turn
          // task doesn't show turn 2's answer when turn 1's node is selected.
          const turnAnswer = (node.spans[0]?.output as { final_answer?: string } | undefined)?.final_answer;
          const answer = turnAnswer ?? (node.spans.length === 0 && node.tone === "pending" ? null : task.final_output);
          return (
            <Section label="Final answer">
              <div className="line-clamp-6 text-[12px] leading-relaxed text-text-muted">{answer ?? "Not reached yet."}</div>
            </Section>
          );
        })()}

      {node.kind === "message" && node.message && (
        <Section label="Follow-up message">
          <div className="text-[12px] leading-relaxed text-text-muted">{node.message.content}</div>
        </Section>
      )}

      {(node.kind === "execute" || node.kind === "sketch") &&
        node.spans.map((span) => (
          <Section key={span.id} label={span.span_type === "tool_call" ? `Live activity · ${span.name}` : "Live activity"}>
            <div className="text-[12px] leading-relaxed text-text-muted">
              {span.span_type === "tool_call" && (
                <>
                  <b className="text-text">{span.name}</b>
                  {/* humanizeValue, not JSON.stringify -- this panel is read
                      by a person inspecting a run, not by a debugger. */}
                  <span className="mt-0.5 block whitespace-pre-wrap">{humanizeValue(span.input)}</span>
                </>
              )}
              {span.span_type === "reasoning" && <>{(span.output as { text?: string })?.text}</>}
            </div>
          </Section>
        ))}

      <Legend />
    </div>
  );
}

function Legend() {
  return (
    <div className="mt-auto flex flex-wrap gap-x-3.5 gap-y-1.5 px-4.5 py-3.5 text-[10.5px] text-text-faint">
      <LegendDot color="bg-status-completed" label="done" />
      <LegendDot color="bg-status-running" label="running" />
      <LegendDot color="bg-role-human" label="escalated" />
      <LegendDot color="bg-status-pending" label="pending" />
    </div>
  );
}

function LegendDot({ color, label }: { color: string; label: string }) {
  return (
    <span className="flex items-center gap-1.5">
      <span className={cn("h-2 w-2 rounded-sm", color)} />
      {label}
    </span>
  );
}
