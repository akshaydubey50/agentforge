"use client";

import useSWR from "swr";
import { api } from "@/lib/api";
import { TopBar } from "@/components/shell/TopBar";
import { ToolCard } from "@/components/tools/ToolCard";
import { SkeletonRows } from "@/components/ui/Skeleton";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogTrigger,
  DialogContent,
  DialogHeader,
  DialogFooter,
  DialogTitle,
  DialogDescription,
  DialogBody,
} from "@/components/ui/dialog";

export default function ToolsPage() {
  const { data: tools, isLoading } = useSWR("tools", () => api.listTools());

  return (
    <>
      <TopBar
        title="Tools"
        subtitle="what the assistant can do"
        actions={
          <Dialog>
            <DialogTrigger asChild>
              <Button size="sm">+ Connect a tool</Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>Connect a custom tool</DialogTitle>
                <DialogDescription>Any MCP server works — yours or someone else&apos;s.</DialogDescription>
              </DialogHeader>
              <DialogBody className="space-y-3">
                <div>
                  <label className="mb-1 block text-[11px] font-extrabold uppercase tracking-wide text-text-faint">
                    What should we call it?
                  </label>
                  <input className="w-full rounded-[var(--rs)] border border-border-strong bg-background px-2.5 py-2 text-[13px] text-text" disabled placeholder="Company directory" />
                </div>
                <div>
                  <label className="mb-1 block text-[11px] font-extrabold uppercase tracking-wide text-text-faint">
                    Server address
                  </label>
                  <input
                    className="mono w-full rounded-[var(--rs)] border border-border-strong bg-background px-2.5 py-2 text-[12px] text-text"
                    disabled
                    placeholder="https://tools.example.com/mcp"
                  />
                  <p className="mt-1 text-[11.5px] text-text-faint">
                    We&apos;ll ask the server what it can do — you don&apos;t list its tools by hand.
                  </p>
                </div>
              </DialogBody>
              <DialogFooter>
                <p className="mr-auto self-center text-[12px] text-text-faint">Coming soon — not wired up yet.</p>
                <Button variant="outline" size="sm" disabled>
                  Connect
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        }
      />
      <div className="flex-1 overflow-y-auto px-5 py-5">
        {isLoading && <SkeletonRows />}
        {tools && (
          <div className="grid grid-cols-2 gap-2.5">
            {tools.map((t) => (
              <ToolCard key={t.name} tool={t} />
            ))}
          </div>
        )}
      </div>
    </>
  );
}
