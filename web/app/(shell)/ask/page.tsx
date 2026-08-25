"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import useSWR, { mutate } from "swr";
import { Check, Copy, History } from "lucide-react";
import { api, isActiveStatus } from "@/lib/api";
import { cn } from "@/lib/utils";
import { taskStatusPill, formatRelativeTime } from "@/lib/statusPill";
import { TopBar } from "@/components/shell/TopBar";
import { SuggestionGrid } from "@/components/ask/SuggestionGrid";
import { Composer } from "@/components/ask/Composer";
import { StepList } from "@/components/ask/StepList";
import { Markdown } from "@/components/ui/Markdown";
import { ApprovalCard } from "@/components/approvals/ApprovalCard";
import { Banner } from "@/components/ui/Banner";
import { Button } from "@/components/ui/button";
import { Pill } from "@/components/ui/Pill";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

const RECENT_TASKS_KEY = "recent-tasks";

function AskPageContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  // Source of truth for "which task is this conversation" is the URL, not
  // local state -- that's what makes a task revisitable: reload the page,
  // or open a link to it later, and it reopens the same conversation
  // instead of dropping back to a blank composer.
  const taskId = searchParams.get("task");

  const [lastRequest, setLastRequest] = useState<string | null>(null);
  const [attachedNames, setAttachedNames] = useState<string[]>([]);
  const [sending, setSending] = useState(false);
  const [copied, setCopied] = useState(false);

  const { data: task } = useSWR(taskId ? ["task", taskId] : null, () => api.getTask(taskId!), {
    // Keep polling (slower) even at awaiting_approval: a decision can resume
    // the task server-side and land it right back in awaiting_approval with
    // a *new* escalation (e.g. a retried tool call hits the same failure) --
    // without this, isActiveStatus alone would stop polling right when a
    // fresh decision is most likely to be needed.
    refreshInterval: (data) => {
      if (!data) return 0;
      if (isActiveStatus(data.status)) return 1500;
      if (data.status === "awaiting_approval") return 4000;
      return 0;
    },
  });

  // Reopening a task from history/a link/a refresh: the request bubble at
  // the top of the thread needs the original text, but that only lives in
  // local state from the submit that created it in THIS tab session. Once
  // the task loads, fall back to the server's request_text so a reopened
  // conversation renders identically to a freshly-submitted one.
  useEffect(() => {
    if (task && lastRequest === null) setLastRequest(task.request_text);
  }, [task, lastRequest]);

  const { data: history } = useSWR(RECENT_TASKS_KEY, () => api.listTasks(12, 0, "all"));

  const needsApproval = task?.status === "awaiting_approval";
  const { data: pendingEscalations, error: escalationError } = useSWR(
    taskId && needsApproval ? ["task-escalations", taskId] : null,
    () => api.listEscalations("pending", 5, 0, taskId!),
    { refreshInterval: 4000 }
  );

  const submit = async (text: string, files: File[]) => {
    setLastRequest(text);
    setAttachedNames(files.map((f) => f.name));
    setSending(true);
    try {
      const created = files.length > 0 ? await api.createTaskWithFiles(text, files) : await api.createTask(text);
      router.push(`/ask?task=${created.id}`, { scroll: false });
      mutate(RECENT_TASKS_KEY);
    } finally {
      setSending(false);
    }
  };

  const resetThread = () => {
    setLastRequest(null);
    setAttachedNames([]);
    setCopied(false);
  };

  const startOver = () => {
    router.push("/ask", { scroll: false });
    resetThread();
  };

  const openTask = (id: string) => {
    if (id === taskId) return;
    router.push(`/ask?task=${id}`, { scroll: false });
    resetThread();
  };

  const copyAnswer = () => {
    if (!task?.final_output) return;
    navigator.clipboard.writeText(task.final_output).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };

  const decideEscalation = async (
    escalationId: string,
    decision: "approve" | "reject" | "take_over",
    overrideOutput?: string
  ) => {
    await api.decideEscalation(escalationId, decision, { overrideOutput });
    // The task resumes running server-side once decided -- refresh both the
    // task (so status/steps update) and this task's escalation list (so the
    // resolved card can settle out) without leaving the page.
    mutate(["task", taskId]);
    mutate(["task-escalations", taskId]);
    mutate(RECENT_TASKS_KEY);
  };

  const hasSteps = !!task && task.subtasks.length > 0;
  const thinking = !!lastRequest && !hasSteps && (sending || !task || isActiveStatus(task.status));

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <TopBar
        title="Ask"
        subtitle="your assistant"
        actions={
          <>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" size="sm">
                  <History className="mr-1.5 h-3.5 w-3.5" />
                  History
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-80">
                <DropdownMenuLabel>Recent asks</DropdownMenuLabel>
                <DropdownMenuSeparator />
                {!history && <div className="px-2 py-3 text-[12.5px] text-text-muted">Loading…</div>}
                {history && history.items.length === 0 && (
                  <div className="px-2 py-3 text-[12.5px] text-text-muted">Nothing yet.</div>
                )}
                {history?.items.map((t) => {
                  const pill = taskStatusPill(t.status);
                  return (
                    <DropdownMenuItem
                      key={t.id}
                      onSelect={() => openTask(t.id)}
                      className={cn("flex-col items-start gap-1 py-2", t.id === taskId && "bg-surface-2")}
                    >
                      <div className="flex w-full items-center gap-2">
                        <Pill kind={pill.kind} live={t.status === "running"}>
                          {pill.label}
                        </Pill>
                        <span className="ml-auto shrink-0 text-[10.5px] text-text-faint">
                          {formatRelativeTime(t.created_at)}
                        </span>
                      </div>
                      <div className="w-full truncate text-[12.5px] text-text">{t.request_text}</div>
                    </DropdownMenuItem>
                  );
                })}
              </DropdownMenuContent>
            </DropdownMenu>
            <Button variant="ghost" size="sm" onClick={startOver}>
              New
            </Button>
          </>
        }
      />
      <div className="flex-1 overflow-y-auto px-5 py-6">
        {/* 560px was a prose reading measure, but steps now render tables,
            file lists and long summaries that were being squeezed into a
            third of a wide screen. Widens with the viewport instead of
            pinning one narrow column. */}
        <div className="mx-auto w-full max-w-[760px] xl:max-w-[920px] 2xl:max-w-[1040px]">
          {!taskId && (
            <>
              <div className="font-serif-display mt-2 text-[25px] font-semibold tracking-tight text-text">
                What can I help you with?
              </div>
              <p className="m-0 text-[13px] text-text-muted">
                I can look things up, read the web, run calculations, and write things for you.
              </p>
              <SuggestionGrid onPick={(text) => submit(text, [])} />
            </>
          )}

          {lastRequest && (
            <div
              className={cn(
                "ml-auto max-w-[80%] rounded-[14px_14px_4px_14px] bg-surface-2 px-3.5 py-2.5 text-[13px] text-text transition-opacity",
                sending && "opacity-55"
              )}
            >
              {lastRequest}
              {attachedNames.length > 0 && (
                <div className="mt-1.5 flex flex-wrap gap-1">
                  {attachedNames.map((name) => (
                    <span key={name} className="mono rounded-full bg-surface-3 px-2 py-0.5 text-[10.5px] text-text-muted">
                      📎 {name}
                    </span>
                  ))}
                </div>
              )}
            </div>
          )}

          {thinking && (
            <div className="mt-2.5 flex items-center gap-2 text-[12.5px] text-text-muted">
              <span className="animate-dot-pulse h-1.5 w-1.5 rounded-full bg-brand" />
              {sending ? "Sending…" : "Understanding your request…"}
            </div>
          )}

          {hasSteps && <StepList subtasks={task!.subtasks} />}

          {needsApproval && (
            <div className="mt-3">
              <Banner kind="w" title="This needs your approval">
                <p>One of the steps needs a decision before the assistant can continue.</p>
              </Banner>
              {escalationError && (
                <p className="mb-2 text-[12px] text-bad">Couldn&apos;t load the decision — try refreshing.</p>
              )}
              {pendingEscalations?.items.map((esc) => (
                <ApprovalCard
                  key={esc.id}
                  escalation={esc}
                  onDecide={(decision, output) => decideEscalation(esc.id, decision, output)}
                />
              ))}
            </div>
          )}

          {task?.status === "failed" && (
            <Banner kind="e" title="That task stopped early">
              <p>Something went wrong partway through. Nothing was charged for unfinished steps.</p>
            </Banner>
          )}

          {task?.status === "completed" && task.final_output && (
            <div className="animate-settle-in mt-3.5">
              <Markdown>{task.final_output}</Markdown>
              <Button variant="ghost" size="sm" className="mt-1 gap-1.5 text-text-faint" onClick={copyAnswer}>
                {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
                {copied ? "Copied" : "Copy answer"}
              </Button>
            </div>
          )}
        </div>
      </div>
      <Composer onSubmit={submit} disabled={sending || (!!taskId && isActiveStatus(task?.status ?? "pending"))} />
    </div>
  );
}

export default function AskPage() {
  // useSearchParams() opts the page out of static rendering unless wrapped
  // in Suspense -- this boundary is what keeps `next build` from warning/
  // bailing on the whole route.
  return (
    <Suspense fallback={<div className="flex min-h-0 flex-1 flex-col" />}>
      <AskPageContent />
    </Suspense>
  );
}
