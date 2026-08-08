"use client";

import { useState } from "react";
import useSWR from "swr";
import { Check, Copy } from "lucide-react";
import { api, isActiveStatus } from "@/lib/api";
import { cn } from "@/lib/utils";
import { TopBar } from "@/components/shell/TopBar";
import { SuggestionGrid } from "@/components/ask/SuggestionGrid";
import { Composer } from "@/components/ask/Composer";
import { StepList } from "@/components/ask/StepList";
import { Banner } from "@/components/ui/Banner";
import { Button } from "@/components/ui/button";

export default function AskPage() {
  const [taskId, setTaskId] = useState<string | null>(null);
  const [lastRequest, setLastRequest] = useState<string | null>(null);
  const [attachedNames, setAttachedNames] = useState<string[]>([]);
  const [sending, setSending] = useState(false);
  const [copied, setCopied] = useState(false);

  const { data: task } = useSWR(taskId ? ["task", taskId] : null, () => api.getTask(taskId!), {
    refreshInterval: (data) => (data && isActiveStatus(data.status) ? 1500 : 0),
  });

  const submit = async (text: string, files: File[]) => {
    setLastRequest(text);
    setAttachedNames(files.map((f) => f.name));
    setSending(true);
    try {
      const created = files.length > 0 ? await api.createTaskWithFiles(text, files) : await api.createTask(text);
      setTaskId(created.id);
    } finally {
      setSending(false);
    }
  };

  const startOver = () => {
    setTaskId(null);
    setLastRequest(null);
    setAttachedNames([]);
    setCopied(false);
  };

  const copyAnswer = () => {
    if (!task?.final_output) return;
    navigator.clipboard.writeText(task.final_output).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };

  const hasSteps = !!task && task.subtasks.length > 0;
  const thinking = !!lastRequest && !hasSteps && (sending || !task || isActiveStatus(task.status));

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <TopBar
        title="Ask"
        subtitle="your assistant"
        actions={
          <Button variant="ghost" size="sm" onClick={startOver}>
            New
          </Button>
        }
      />
      <div className="flex-1 overflow-y-auto px-5 py-6">
        <div className="mx-auto max-w-[560px]">
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

          {task?.status === "awaiting_approval" && (
            <Banner kind="w" title="This needs your approval">
              <p>
                One of the steps needs a decision before the assistant can continue. Head to{" "}
                <a href="/approvals">Approvals</a> to review it.
              </p>
            </Banner>
          )}

          {task?.status === "failed" && (
            <Banner kind="e" title="That task stopped early">
              <p>Something went wrong partway through. Nothing was charged for unfinished steps.</p>
            </Banner>
          )}

          {task?.status === "completed" && task.final_output && (
            <div className="animate-settle-in mt-3.5">
              <p className="text-[14.5px] leading-[1.7] text-text">{task.final_output}</p>
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
