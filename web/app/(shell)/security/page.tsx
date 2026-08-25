"use client";

import useSWR from "swr";
import type { ReactNode } from "react";
import { AlertTriangle, KeyRound, LockKeyhole, Shield, ShieldAlert } from "lucide-react";
import { api } from "@/lib/api";
import { TopBar } from "@/components/shell/TopBar";

const SECURITY_EVENTS = [
  { stage: "Input", risk: "Prompt injection", detector: "guardrail", decision: "Safe metadata only", status: "API gap" },
  { stage: "Retrieval", risk: "Malicious retrieved content", detector: "retrieval guard", decision: "Do not render payload", status: "API gap" },
  { stage: "MCP discovery", risk: "Untrusted tool description", detector: "trust review", decision: "Review required", status: "API gap" },
  { stage: "Output", risk: "Secret leakage", detector: "secret scanner", decision: "Redact or block", status: "API gap" },
];

export default function SecurityPage() {
  const { data: summary } = useSWR("system-summary", () => api.getSystemSummary(), { refreshInterval: 5000 });

  return (
    <>
      <TopBar title="Security" subtitle="guardrails and trust metadata without raw sensitive payloads" />
      <div className="flex-1 overflow-y-auto px-7 py-5.5">
        <div className="mb-5 grid grid-cols-4 gap-3 max-[1080px]:grid-cols-2 max-[700px]:grid-cols-1">
          <Kpi icon={<Shield className="h-4 w-4" />} label="Guardrail events" value="API gap" />
          <Kpi icon={<ShieldAlert className="h-4 w-4" />} label="Policy denies" value="API gap" />
          <Kpi icon={<KeyRound className="h-4 w-4" />} label="MCP trust changes" value="API gap" />
          <Kpi icon={<LockKeyhole className="h-4 w-4" />} label="Tool failures" value={summary?.tool_calls.failed ?? "Loading"} />
        </div>

        <section className="overflow-hidden rounded-[8px] border border-border bg-surface">
          <div className="flex items-center gap-2 border-b border-border px-4 py-3">
            <AlertTriangle className="h-4 w-4 text-role-human" />
            <h2 className="text-[13.5px] font-semibold text-text">Safe security event model</h2>
          </div>
          <div className="divide-y divide-border">
            {SECURITY_EVENTS.map((event) => (
              <div key={`${event.stage}-${event.risk}`} className="grid grid-cols-[130px_1fr_150px_160px_100px] gap-4 px-4 py-3 text-[12px] max-[980px]:grid-cols-1">
                <div className="font-mono font-semibold text-text">{event.stage}</div>
                <div className="text-text-muted">{event.risk}</div>
                <div className="text-text-muted">{event.detector}</div>
                <div className="text-text-muted">{event.decision}</div>
                <div className="text-role-human">{event.status}</div>
              </div>
            ))}
          </div>
        </section>

        <div className="mt-5 rounded-[8px] border border-border bg-rail p-4 text-[12.5px] leading-relaxed text-text-muted">
          This page intentionally avoids raw secrets, private emails, and attack payloads. Phase 9B should add a safe guardrail-event endpoint with stage, risk category, detector, decision, timestamp, redaction state, and run/node reference.
        </div>
      </div>
    </>
  );
}

function Kpi({ icon, label, value }: { icon: ReactNode; label: string; value: string | number }) {
  return (
    <div className="rounded-[8px] border border-border bg-surface px-4 py-3.5">
      <div className="flex items-center gap-2 text-text-faint">
        {icon}
        <span className="text-[10.5px] uppercase tracking-wide">{label}</span>
      </div>
      <div className="mt-2 font-mono text-[19px] font-semibold text-text">{value}</div>
    </div>
  );
}
