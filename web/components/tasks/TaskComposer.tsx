"use client";

import { useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { api, ATTACHABLE_EXTENSIONS } from "@/lib/api";
import { Button } from "@/components/ui/button";

export function TaskComposer() {
  const router = useRouter();
  const [text, setText] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const submit = async () => {
    const requestText = text.trim();
    if (!requestText || submitting) return;
    setSubmitting(true);
    try {
      const created = files.length > 0 ? await api.createTaskWithFiles(requestText, files) : await api.createTask(requestText);
      router.push(`/tasks/${created.id}`);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="mb-5 rounded-[var(--rm)] border border-border bg-surface p-4">
      <div className="mb-2.5 text-[11px] font-semibold uppercase tracking-wide text-text-faint">
        Submit a new task
      </div>
      <textarea
        className="w-full resize-none rounded-[var(--rs)] border border-border bg-background px-3.5 py-3 text-[13px] leading-relaxed text-text placeholder:text-text-faint focus-visible:border-role-supervisor"
        rows={2}
        placeholder="Using our metrics database, find Cedar Analytics' revenue for 2026-Q1 and 2026-Q2, calculate the percentage growth between them, and save a short summary report to report.txt."
        value={text}
        disabled={submitting}
        onChange={(e) => setText(e.target.value)}
      />
      {files.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {files.map((f, i) => (
            <span key={`${f.name}-${i}`} className="mono flex items-center gap-1.5 rounded-full bg-surface-3 px-2.5 py-1 text-[11px] text-text">
              {f.name}
              <button onClick={() => setFiles((fs) => fs.filter((_, j) => j !== i))} aria-label={`Remove ${f.name}`}>
                &times;
              </button>
            </span>
          ))}
        </div>
      )}
      <div className="mt-3 flex items-center justify-between">
        <button
          type="button"
          className="text-[11.5px] text-text-faint hover:text-text-muted"
          onClick={() => fileInputRef.current?.click()}
          disabled={submitting}
        >
          📎 attach {ATTACHABLE_EXTENSIONS.join(" ")}
        </button>
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept={ATTACHABLE_EXTENSIONS.join(",")}
          className="hidden"
          onChange={(e) => {
            if (e.target.files) setFiles((fs) => [...fs, ...Array.from(e.target.files!)]);
            e.target.value = "";
          }}
        />
        <Button
          size="sm"
          className="bg-role-supervisor text-background hover:bg-role-supervisor/90"
          disabled={submitting || !text.trim()}
          onClick={submit}
        >
          {submitting ? "Submitting…" : "Run task →"}
        </Button>
      </div>
    </div>
  );
}
