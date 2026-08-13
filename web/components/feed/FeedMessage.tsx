import type { FeedItem } from "@/lib/traceFeed";
import { spanDurationLabel } from "@/lib/traceFeed";
import { ROLE_META } from "@/lib/agentRoles";
import { Markdown } from "@/components/ui/Markdown";
import { cn } from "@/lib/utils";

function Avatar({ role }: { role: keyof typeof ROLE_META }) {
  const meta = ROLE_META[role];
  return (
    <div className={cn("flex h-8 w-8 flex-none items-center justify-center rounded-lg text-[12px] font-bold", meta.bgClass, meta.colorClass)}>
      {meta.initial}
    </div>
  );
}

function Header({ role, label, spanType, time }: { role: keyof typeof ROLE_META; label: string; spanType: string; time: string }) {
  const meta = ROLE_META[role];
  return (
    <div className="mb-1.5 flex items-baseline gap-2">
      <span className={cn("text-[12.5px] font-semibold", meta.colorClass)}>{label}</span>
      <span className="mono rounded border border-border px-1.5 py-px text-[10px] text-text-faint">{spanType}</span>
      <span className="mono text-[10.5px] text-text-faint">{time}</span>
    </div>
  );
}

function Bubble({ children, dim, className }: { children: React.ReactNode; dim?: boolean; className?: string }) {
  return (
    <div
      className={cn(
        "rounded-[3px_12px_12px_12px] border border-border bg-surface px-4 py-2.5 text-[13.5px] leading-relaxed",
        dim ? "text-text-muted" : "text-text",
        className
      )}
    >
      {children}
    </div>
  );
}

function KV({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <span className="text-text-faint">{label}:</span> {children}
    </div>
  );
}

function ToolCallBody({ item }: { item: FeedItem }) {
  const span = item.span!;
  const output = span.output as { success?: boolean; output?: Record<string, unknown>; error?: string } | undefined;
  const status = span.ended_at === null ? "running" : output?.success === false || span.status === "error" ? "err" : "ok";
  const inputEntries = Object.entries(span.input ?? {});

  return (
    <div className="mt-2 overflow-hidden rounded-[9px] border border-border bg-canvas">
      <div className="flex items-center justify-between border-b border-border bg-white/[0.02] px-3.5 py-2">
        <span className="mono text-[12px] font-medium text-role-reviewer">⚡ {span.name}</span>
        <span
          className={cn(
            "rounded-full px-2 py-0.5 text-[10px]",
            status === "ok" && "bg-status-completed/15 text-status-completed",
            status === "running" && "bg-status-running/15 text-status-running",
            status === "err" && "bg-status-failed/15 text-status-failed"
          )}
        >
          {status === "ok" && `ok · ${spanDurationLabel(span)}`}
          {status === "running" && "running"}
          {status === "err" && `error · ${spanDurationLabel(span)}`}
        </span>
      </div>
      <div className="mono space-y-1 px-3.5 py-2.5 text-[11.5px] leading-relaxed text-text-muted">
        {inputEntries.map(([k, v]) => (
          <KV key={k} label={k}>
            {typeof v === "string" ? v : JSON.stringify(v)}
          </KV>
        ))}
        {output?.success && (
          <KV label="output">
            <span className="text-status-completed">{JSON.stringify(output.output)}</span>
          </KV>
        )}
        {output?.success === false && <KV label="error"><span className="text-status-failed">{output.error}</span></KV>}
      </div>
    </div>
  );
}

export function FeedMessage({ item }: { item: FeedItem }) {
  if (item.kind === "sketch") {
    const span = item.span!;
    const out = span.output as { outline?: string[]; confidence?: number; reasoning?: string };
    return (
      <div className="flex gap-3">
        <Avatar role="supervisor" />
        <div className="min-w-0 flex-1">
          <Header role="supervisor" label="Supervisor" spanType="sketch" time={spanDurationLabel(span)} />
          <Bubble>
            {out.reasoning}
            {out.outline && out.outline.length > 0 && (
              <ul className="mt-2 space-y-1 pl-0.5 text-[12.5px] text-text-muted">
                {out.outline.map((step, i) => (
                  <li key={i} className="flex gap-2">
                    <span className="mono text-role-supervisor">·</span>
                    {step}
                  </li>
                ))}
              </ul>
            )}
            <div className="mt-2 text-[11px] text-text-faint">non-binding outline · confidence {out.confidence}/5</div>
          </Bubble>
        </div>
      </div>
    );
  }

  if (item.kind === "agent_step") {
    const span = item.span!;
    const out = span.output as { next_action?: string; subtask_description?: string; tool_name?: string; rationale?: string };
    return (
      <div className="flex gap-3">
        <Avatar role="supervisor" />
        <div className="min-w-0 flex-1">
          <Header role="supervisor" label="Supervisor" spanType="agent_step" time={spanDurationLabel(span)} />
          <Bubble dim className="text-[12.5px]">
            {out.next_action === "finish" ? (
              <>Decided the request is complete — moving to synthesis.</>
            ) : (
              <>
                Next step: <span className="text-text">{out.subtask_description}</span>
                {out.tool_name && out.tool_name !== "none" && (
                  <span className="mono text-text-faint"> → {out.tool_name}</span>
                )}
              </>
            )}
          </Bubble>
        </div>
      </div>
    );
  }

  if (item.kind === "tool_call" || item.kind === "reasoning") {
    const span = item.span!;
    const label = item.subtask ? `Specialist · subtask #${item.subtask.position}` : "Specialist";
    return (
      <div className="flex gap-3">
        <Avatar role="specialist" />
        <div className="min-w-0 flex-1">
          <Header role="specialist" label={label} spanType={span.span_type} time={spanDurationLabel(span)} />
          {item.rationale && <Bubble dim>{item.rationale}</Bubble>}
          {item.kind === "tool_call" ? (
            <ToolCallBody item={item} />
          ) : (
            <Bubble dim={!item.rationale}>{(span.output as { text?: string })?.text}</Bubble>
          )}
        </div>
      </div>
    );
  }

  if (item.kind === "review") {
    const span = item.span!;
    const out = span.output as { score?: number; verdict?: string; feedback?: string };
    const verdictClass =
      out.verdict === "pass"
        ? "bg-status-completed/15 text-status-completed"
        : out.verdict === "reject"
          ? "bg-status-revision/15 text-status-revision"
          : "bg-role-human/15 text-role-human";
    const label = item.subtask ? `Reviewer · subtask #${item.subtask.position}` : "Reviewer";
    return (
      <div className="flex gap-3">
        <Avatar role="reviewer" />
        <div className="min-w-0 flex-1">
          <Header role="reviewer" label={label} spanType="review" time={spanDurationLabel(span)} />
          <Bubble dim>
            {out.feedback}{" "}
            <span className={cn("ml-1 inline-flex items-center rounded-full px-2.5 py-0.5 text-[11px] font-semibold", verdictClass)}>
              {out.verdict === "pass" ? "✓ pass" : out.verdict === "reject" ? "↻ reject" : "⏸ escalate"} · {out.score}/5
            </span>
          </Bubble>
        </div>
      </div>
    );
  }

  if (item.kind === "synthesize") {
    const span = item.span!;
    const out = span.output as { final_answer?: string };
    return (
      <div className="flex gap-3">
        <Avatar role="supervisor" />
        <div className="min-w-0 flex-1">
          <Header role="supervisor" label="Supervisor" spanType="synthesize" time={spanDurationLabel(span)} />
          <Bubble>
            <Markdown className="text-[13.5px]">{out.final_answer ?? ""}</Markdown>
          </Bubble>
        </div>
      </div>
    );
  }

  return null;
}
