"use client";

import useSWR from "swr";
import { ShieldCheck, Wrench } from "lucide-react";
import { api } from "@/lib/api";
import { catalogEntry } from "@/lib/toolCatalog";
import { TopBar } from "@/components/shell/TopBar";
import { Pill } from "@/components/ui/Pill";
import { SkeletonRows } from "@/components/ui/Skeleton";

export default function ToolsPage() {
  const { data: topology, isLoading } = useSWR("system-topology", () => api.getSystemTopology(), {
    revalidateOnFocus: false,
  });

  const tools = topology?.tools ?? [];

  return (
    <>
      <TopBar title="Tools" subtitle="read-only registry of native and MCP tools" />
      <div className="flex-1 overflow-y-auto px-5 py-5">
        <div className="mb-4 rounded-[8px] border border-border bg-rail px-4 py-3 text-[12.5px] leading-relaxed text-text-muted">
          AgentForge does not support arbitrary tool creation from this UI. Tool availability, source, and approval posture come from the backend runtime topology.
        </div>

        {isLoading && <SkeletonRows />}
        {tools.length > 0 && (
          <div className="grid grid-cols-2 gap-3 max-[980px]:grid-cols-1">
            {tools.map((tool) => {
              const entry = catalogEntry(tool.name, tool.summary);
              return (
                <section key={`${tool.server ?? "native"}-${tool.name}`} className="rounded-[8px] border border-border bg-surface p-4">
                  <div className="flex items-start gap-3">
                    <div className="flex h-9 w-9 flex-none items-center justify-center rounded-[8px] border border-border bg-surface-2 text-[15px]">
                      {entry.icon || <Wrench className="h-4 w-4" />}
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <h2 className="truncate text-[13.5px] font-semibold text-text">{entry.label}</h2>
                        <Pill kind="ok">available</Pill>
                        <span className="rounded-full bg-surface-3 px-2 py-0.5 text-[10.5px] text-text-faint">
                          {tool.server ? "MCP" : "native"}
                        </span>
                      </div>
                      <p className="mt-1 text-[12px] leading-relaxed text-text-muted">{entry.friendlyDescription || tool.summary}</p>
                    </div>
                  </div>

                  <div className="mt-4 grid grid-cols-2 gap-2 text-[11.5px]">
                    <Fact label="Source" value={tool.server ?? "AgentForge native"} />
                    <Fact label="Policy posture" value={tool.requires_approval ? "REQUIRE_APPROVAL" : "ALLOW"} />
                    <Fact label="Action type" value="Not exposed by API" />
                    <Fact label="Execution safety" value="Not exposed by API" />
                    <Fact label="Verification" value="Trace/tool result when recorded" />
                  </div>

                  <div className="mt-3 flex items-start gap-2 rounded-[7px] border border-border bg-rail px-3 py-2 text-[11.5px] leading-relaxed text-text-muted">
                    <ShieldCheck className="mt-0.5 h-3.5 w-3.5 flex-none" />
                    Typed schema details are not exposed by the current registry endpoint. Phase 9B should add a schema detail endpoint before showing argument contracts.
                  </div>
                </section>
              );
            })}
          </div>
        )}
        {!isLoading && tools.length === 0 && <div className="rounded-[8px] border border-border bg-surface px-5 py-8 text-[12.5px] text-text-faint">No tools registered.</div>}
      </div>
    </>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-[7px] border border-border bg-rail px-3 py-2">
      <div className="text-[10px] uppercase tracking-[0.08em] text-text-faint">{label}</div>
      <div className="mt-0.5 truncate font-mono text-text">{value}</div>
    </div>
  );
}
