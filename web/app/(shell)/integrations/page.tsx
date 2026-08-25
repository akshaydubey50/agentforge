"use client";

import { useState } from "react";
import useSWR from "swr";
import { api, googleLoginUrl } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { SkeletonRows } from "@/components/ui/Skeleton";
import { cn } from "@/lib/utils";

const SCOPE_LABELS: Record<string, string> = {
  "https://www.googleapis.com/auth/drive.readonly": "Drive (read-only)",
  "https://www.googleapis.com/auth/gmail.readonly": "Gmail (read-only)",
  "https://www.googleapis.com/auth/gmail.compose": "Gmail drafts",
  "https://www.googleapis.com/auth/userinfo.email": "Email address",
  "https://www.googleapis.com/auth/userinfo.profile": "Name & photo",
  openid: "Sign-in",
};

function IntegrationsContent() {
  const { data, isLoading, mutate } = useSWR("google-status", () => api.getGoogleStatus());
  const [banner, setBanner] = useState<{ kind: "ok" | "err" | "info"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const disconnect = async () => {
    setBusy(true);
    try {
      await api.disconnectGoogle();
      setBanner({ kind: "info", text: "Google account disconnected." });
      mutate();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex-1 overflow-y-auto px-7 py-5.5">
      <div className="mx-auto max-w-[820px]">
        <h1 className="mb-1.5 text-[17px] font-semibold text-text">Integrations</h1>
        <p className="mb-5.5 text-[12.5px] text-text-faint">
          Connect external accounts so the agent&apos;s tools can use them. Access is read-only.
        </p>

        {banner && (
          <div
            className={cn(
              "mb-4 rounded-[var(--rm)] border px-4 py-2.5 text-[12.5px]",
              banner.kind === "ok" && "border-status-completed/40 bg-status-completed/10 text-status-completed",
              banner.kind === "err" && "border-status-failed/40 bg-status-failed/10 text-status-failed",
              banner.kind === "info" && "border-border bg-surface text-text-muted"
            )}
          >
            {banner.text}
          </div>
        )}

        {isLoading && !data && <SkeletonRows rows={2} />}

        {data && (
          <div className="rounded-[var(--rm)] border border-border bg-surface p-5">
            <div className="flex items-start gap-3.5">
              <div className="flex h-10 w-10 flex-none items-center justify-center rounded-lg bg-surface-3 text-[18px]">G</div>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="text-[14px] font-semibold text-text">Google Workspace</span>
                  {data.connected ? (
                    <span className="rounded-full bg-status-completed/15 px-2 py-0.5 text-[10.5px] font-semibold text-status-completed">
                      connected
                    </span>
                  ) : (
                    <span className="rounded-full bg-surface-3 px-2 py-0.5 text-[10.5px] font-semibold text-text-faint">
                      not connected
                    </span>
                  )}
                </div>
                <div className="mt-0.5 text-[12px] text-text-muted">
                  Drive &amp; Gmail search/read for the agent. Google&apos;s consent screen opens in this window.
                </div>

                {data.connected && data.google_email && (
                  <div className="mono mt-2 text-[11.5px] text-text-muted">{data.google_email}</div>
                )}
                {data.connected && data.scopes.length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {data.scopes.map((s) => (
                      <span key={s} className="rounded-md bg-surface-3 px-2 py-0.5 text-[10.5px] text-text-muted">
                        {SCOPE_LABELS[s] ?? s}
                      </span>
                    ))}
                  </div>
                )}
              </div>

              <div className="flex-none">
                {!data.configured ? (
                  <span className="text-[11.5px] text-text-faint">Not configured on server</span>
                ) : data.connected ? (
                  <Button variant="outline" size="sm" disabled={busy} onClick={disconnect}>
                    Disconnect
                  </Button>
                ) : (
                  // Plain anchor -> real browser navigation to the backend
                  // /google/login endpoint, which 307s to Google. Never a
                  // fetch. next=/integrations so success lands back here
                  // (with a fresh connection) instead of the dashboard root.
                  <a href={googleLoginUrl("/integrations")}>
                    <Button size="sm" className="bg-role-supervisor text-background hover:bg-role-supervisor/90">
                      Reconnect
                    </Button>
                  </a>
                )}
              </div>
            </div>

            {!data.configured && (
              <div className="mt-4 rounded-[var(--rs)] border border-dashed border-border-strong bg-canvas px-3.5 py-2.5 text-[11.5px] leading-relaxed text-text-muted">
                An admin needs to set <span className="mono">GOOGLE_CLIENT_ID</span> and{" "}
                <span className="mono">GOOGLE_CLIENT_SECRET</span> (OAuth Web-application credentials from Google Cloud
                Console) and add the callback URL as an authorized redirect URI. Until then, the Reconnect button is
                disabled and the Drive/Gmail tools stay out of the agent&apos;s toolset.
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

export default function IntegrationsPage() {
  return <IntegrationsContent />;
}
