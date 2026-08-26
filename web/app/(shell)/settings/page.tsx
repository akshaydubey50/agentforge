"use client";

import useSWR from "swr";
import { api } from "@/lib/api";
import { TopBar } from "@/components/shell/TopBar";

export default function SettingsPage() {
  const { data: topology } = useSWR("system-topology", () => api.getSystemTopology(), { revalidateOnFocus: false });
  const { data: google } = useSWR("google-status", () => api.getGoogleStatus());
  const model = topology?.subsystems.find((s) => s.id === "models");
  const runtime = topology?.subsystems.find((s) => s.id === "runtime");
  const memory = topology?.subsystems.find((s) => s.id === "memory");

  return (
    <>
      <TopBar title="Settings" subtitle="read-only runtime configuration and capability status" />
      <div className="flex-1 overflow-y-auto px-5 py-5">
        <p className="mb-4 rounded-[8px] border border-border bg-rail px-4 py-3 text-[12.5px] leading-relaxed text-text-muted">
          This page exposes operator-readable status only. Model, policy, and runtime limits are code/config defined unless a backend mutation API explicitly exists.
        </p>

        <div className="grid grid-cols-2 gap-4 max-[1000px]:grid-cols-1">
          <SettingsGroup title="Models" rows={model?.facts.map((fact) => [fact.label, fact.value]) ?? [["Status", "Not exposed"]]} />
          <SettingsGroup title="Runtime" rows={runtime?.facts.map((fact) => [fact.label, fact.value]) ?? [["Status", "Not exposed"]]} />
          <SettingsGroup title="Context and Memory" rows={memory?.facts.map((fact) => [fact.label, fact.value]) ?? [["Status", "Not exposed"]]} />
          <SettingsGroup
            title="Integrations"
            rows={[
              ["Google", google ? (google.connected ? `Connected as ${google.google_email ?? "Google user"}` : google.configured ? "Needs consent" : "Server not configured") : "Loading"],
              ["Gmail send", "Not supported"],
            ]}
          />
          <SettingsGroup title="Observability" rows={[["Analytics", "Read-only /v1/analytics"], ["Live stream", "SSE narration plus durable refetch"]]} />
          <SettingsGroup title="Security" rows={[["Policies", "Code-defined explorer"], ["Guardrail event API", "Backend gap"], ["MCP trust metadata", "Backend gap"]]} />
        </div>
      </div>
    </>
  );
}

function SettingsGroup({ title, rows }: { title: string; rows: [string, string][] }) {
  return (
    <section className="rounded-[8px] border border-border bg-surface">
      <div className="border-b border-border px-4 py-3 text-[13.5px] font-semibold text-text">{title}</div>
      <div className="divide-y divide-border">
        {rows.map(([label, value]) => (
          <div key={label} className="flex items-start justify-between gap-4 px-4 py-3 text-[12.5px]">
            <span className="text-text-muted">{label}</span>
            <span className="max-w-[65%] text-right font-mono text-text">{value}</span>
          </div>
        ))}
      </div>
    </section>
  );
}
