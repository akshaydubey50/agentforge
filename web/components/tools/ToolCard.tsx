import type { ToolInfo } from "@/lib/api";
import { catalogEntry } from "@/lib/toolCatalog";
import { Pill } from "@/components/ui/Pill";

export function ToolCard({ tool }: { tool: ToolInfo }) {
  const entry = catalogEntry(tool.name, tool.description);
  return (
    <div className="flex items-start gap-2.5 rounded-[var(--rm)] border border-border bg-surface p-3.5">
      <div className="flex h-8 w-8 flex-none items-center justify-center rounded-[9px] bg-surface-2 text-[15px]">
        {entry.icon}
      </div>
      <div>
        <div className="flex flex-wrap items-center gap-1.5 text-[13.5px] font-semibold text-text">
          {entry.label}
          <Pill kind="ok">On</Pill>
        </div>
        <div className="mt-0.5 text-[12px] text-text-muted">{entry.friendlyDescription}</div>
      </div>
    </div>
  );
}
