"use client";

import useSWR from "swr";
import { AlertTriangle, Plug, ShieldAlert } from "lucide-react";
import { api } from "@/lib/api";
import { TopBar } from "@/components/shell/TopBar";
import { SkeletonRows } from "@/components/ui/Skeleton";
import { Pill } from "@/components/ui/Pill";

export default function McpPage() {
  const { data: topology, isLoading } = useSWR("system-topology", () => api.getSystemTopology(), {
    revalidateOnFocus: false,
  });

  const mcpTools = (topology?.tools ?? []).filter((tool) => tool.server);
  const servers = Array.from(new Set(mcpTools.map((tool) => tool.server).filter(Boolean))) as string[];

  return (
    <>
      <TopBar title="MCP" subtitle="server discovery and trust review" />
      <div className="flex-1 overflow-y-auto px-5 py-5">
        <div className="mb-4 rounded-[8px] border border-role-human/45 bg-role-human/8 px-4 py-3 text-[12.5px] leading-relaxed text-text-muted">
          The current topology endpoint exposes MCP tool source but not schema fingerprints, review decisions, or changed-capability warnings. Those are Phase 9B backend gaps before this page can mark MCP tools as trusted.
        </div>

        {isLoading && <SkeletonRows />}

        {!isLoading && servers.length === 0 && (
          <section className="rounded-[8px] border border-border bg-surface px-5 py-9 text-center">
            <Plug className="mx-auto h-7 w-7 text-text-faint" />
            <h2 className="mt-3 text-[15px] font-semibold text-text">No MCP servers discovered</h2>
            <p className="mx-auto mt-1 max-w-[54ch] text-[12.5px] leading-relaxed text-text-muted">
              Native tools may still be available. MCP configuration and trust review state are not exposed through the current frontend API.
            </p>
          </section>
        )}

        <div className="space-y-4">
          {servers.map((server) => {
            const tools = mcpTools.filter((tool) => tool.server === server);
            return (
              <section key={server} className="overflow-hidden rounded-[8px] border border-border bg-surface">
                <div className="flex items-center gap-3 border-b border-border px-4 py-3">
                  <Plug className="h-4 w-4 text-text-muted" />
                  <div className="min-w-0 flex-1">
                    <h2 className="truncate text-[13.5px] font-semibold text-text">{server}</h2>
                    <div className="mt-0.5 text-[11px] text-text-faint">{tools.length} discovered tool{tools.length === 1 ? "" : "s"}</div>
                  </div>
                  <Pill kind="wn">review needed</Pill>
                </div>
                <div className="divide-y divide-border">
                  {tools.map((tool) => (
                    <div key={tool.name} className="grid grid-cols-[minmax(180px,0.8fr)_minmax(220px,1fr)_150px_160px] gap-4 px-4 py-3 text-[12px] max-[980px]:grid-cols-1">
                      <div>
                        <div className="font-mono font-semibold text-text">{tool.name}</div>
                        <div className="mt-0.5 text-text-faint">source: MCP</div>
                      </div>
                      <div className="text-text-muted">{tool.summary}</div>
                      <div className="text-text-muted">{tool.requires_approval ? "REQUIRE_APPROVAL" : "ALLOW"}</div>
                      <div className="flex items-center gap-2 text-role-human">
                        <ShieldAlert className="h-3.5 w-3.5" />
                        fingerprint unavailable
                      </div>
                    </div>
                  ))}
                </div>
              </section>
            );
          })}
        </div>

        <section className="mt-5 rounded-[8px] border border-border bg-rail p-4">
          <div className="flex items-start gap-2 text-[12.5px] leading-relaxed text-text-muted">
            <AlertTriangle className="mt-0.5 h-4 w-4 flex-none text-role-human" />
            Trust review required should be backed by durable server schema fingerprint/version, classification, execution safety, last discovery timestamp, and changed-schema warnings. This page intentionally does not show MCP tools as trusted without those fields.
          </div>
        </section>
      </div>
    </>
  );
}
