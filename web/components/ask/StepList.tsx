import type { SubtaskOut } from "@/lib/api";
import { cn } from "@/lib/utils";

function tkClasses(status: SubtaskOut["status"]) {
  if (status === "done") return "bg-ok text-white";
  if (status === "escalated" || status === "failed") return "bg-bad text-white";
  if (status === "running") return "bg-brand-wash text-brand animate-now-pulse";
  if (status === "needs_revision") return "bg-warn-wash text-warn";
  return "bg-surface-3 text-text-faint";
}

function stepGlyph(status: SubtaskOut["status"]) {
  switch (status) {
    case "done":
      return "✓";
    case "skipped":
      return "–";
    case "escalated":
    case "failed":
      return "✕";
    case "needs_revision":
      return "↻";
    case "running":
      return "◐";
    default:
      return "◌";
  }
}

export function StepList({ subtasks }: { subtasks: SubtaskOut[] }) {
  const ordered = [...subtasks].sort((a, b) => a.position - b.position);
  return (
    <div className="mt-3.5">
      {ordered.map((s) => (
        <div key={s.id} className="animate-step-in flex items-center gap-2 py-1 text-[13px] text-text">
          <span
            className={cn(
              "flex h-[17px] w-[17px] flex-none items-center justify-center rounded-full text-[9px] font-extrabold",
              tkClasses(s.status)
            )}
          >
            {stepGlyph(s.status)}
          </span>
          {s.description}
          {s.status === "done" && s.output && (
            <>
              {" — "}
              <b className="font-semibold">{truncate(s.output, 80)}</b>
            </>
          )}
        </div>
      ))}
    </div>
  );
}

function truncate(text: string, max: number) {
  return text.length > max ? `${text.slice(0, max)}…` : text;
}
