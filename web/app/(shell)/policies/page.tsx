"use client";

import useSWR from "swr";
import { ShieldCheck, SlidersHorizontal } from "lucide-react";
import { api } from "@/lib/api";
import { TopBar } from "@/components/shell/TopBar";
import { Pill } from "@/components/ui/Pill";

const RULES = [
  { action: "READ", risk: "LOW", decision: "ALLOW", note: "Search, inspect, or retrieve non-mutating data." },
  { action: "LOCAL_WRITE", risk: "MEDIUM", decision: "REQUIRE_APPROVAL", note: "Writes to local/project artifacts require explicit user review when policy classifies them as medium risk." },
  { action: "EXTERNAL_WRITE", risk: "MEDIUM", decision: "REQUIRE_APPROVAL", note: "External side effects such as Gmail draft creation pause for human approval." },
  { action: "DESTRUCTIVE", risk: "HIGH", decision: "DENY", note: "Destructive or irreversible actions are denied by deterministic policy." },
];

export default function PoliciesPage() {
  const { data: topology } = useSWR("system-topology", () => api.getSystemTopology(), { revalidateOnFocus: false });

  return (
    <>
      <TopBar title="Policy Explorer" subtitle="read-only deterministic policy posture" />
      <div className="flex-1 overflow-y-auto px-7 py-5.5">
        <div className="mb-5 rounded-[8px] border border-border bg-rail p-4 text-[12.5px] leading-relaxed text-text-muted">
          Policies are code-defined in the current backend. This is an explorer, not a policy builder.
        </div>

        <section className="mb-5 overflow-hidden rounded-[8px] border border-border bg-surface">
          <div className="flex items-center gap-2 border-b border-border px-4 py-3">
            <SlidersHorizontal className="h-4 w-4 text-text-muted" />
            <h2 className="text-[13.5px] font-semibold text-text">Policy matrix</h2>
          </div>
          <div className="divide-y divide-border">
            {RULES.map((rule) => (
              <div key={rule.action} className="grid grid-cols-[140px_120px_170px_1fr] gap-4 px-4 py-3 text-[12.5px] max-[900px]:grid-cols-1">
                <div className="font-mono font-semibold text-text">{rule.action}</div>
                <div className="text-text-muted">{rule.risk}</div>
                <div>
                  <Pill kind={rule.decision === "ALLOW" ? "ok" : rule.decision === "DENY" ? "bad" : "wn"}>{rule.decision}</Pill>
                </div>
                <div className="text-text-muted">{rule.note}</div>
              </div>
            ))}
          </div>
        </section>

        <section className="overflow-hidden rounded-[8px] border border-border bg-surface">
          <div className="flex items-center gap-2 border-b border-border px-4 py-3">
            <ShieldCheck className="h-4 w-4 text-text-muted" />
            <h2 className="text-[13.5px] font-semibold text-text">Effective tool posture</h2>
          </div>
          <div className="divide-y divide-border">
            {(topology?.tools ?? []).map((tool) => (
              <div key={`${tool.server ?? "native"}-${tool.name}`} className="grid grid-cols-[minmax(160px,0.8fr)_120px_160px_1fr] gap-4 px-4 py-3 text-[12px] max-[900px]:grid-cols-1">
                <div className="font-mono font-semibold text-text">{tool.name}</div>
                <div className="text-text-muted">{tool.server ? "MCP" : "native"}</div>
                <div>
                  <Pill kind={tool.requires_approval ? "wn" : "ok"}>{tool.requires_approval ? "APPROVAL" : "ALLOW"}</Pill>
                </div>
                <div className="text-text-muted">{tool.summary}</div>
              </div>
            ))}
            {topology && topology.tools.length === 0 && <div className="px-4 py-5 text-[12px] text-text-faint">No registered tools.</div>}
          </div>
        </section>
      </div>
    </>
  );
}
