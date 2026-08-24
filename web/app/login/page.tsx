"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { googleLoginUrl } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

// Outside the (shell) route group on purpose -- a signed-out visitor must
// never see the Sidebar/rail (it assumes a logged-in user for things like
// pending-escalation counts), and middleware.ts redirects every other route
// here when the session cookie is missing.
function LoginContent() {
  const searchParams = useSearchParams();
  const status = searchParams.get("status");
  const reason = searchParams.get("reason");

  const banner =
    status === "error"
      ? { kind: "err" as const, text: reason || "Couldn't sign in with Google." }
      : status === "cancelled"
        ? { kind: "info" as const, text: "Sign-in cancelled." }
        : null;

  return (
    <div className="flex h-screen items-center justify-center bg-canvas px-4">
      <div className="w-full max-w-[360px] rounded-[var(--rm)] border border-border bg-surface p-7 text-center">
        <div className="mx-auto mb-4 flex h-11 w-11 items-center justify-center rounded-[10px] bg-gradient-to-br from-role-supervisor to-[#6D5AE0] text-[15px] font-bold text-background">
          AF
        </div>
        <h1 className="mb-1.5 text-[16px] font-semibold text-text">Sign in to AgentForge</h1>
        <p className="mb-5.5 text-[12.5px] leading-relaxed text-text-faint">
          Tasks, escalations, and memory are private to your account. Sign in with Google to continue.
        </p>

        {banner && (
          <div
            className={cn(
              "mb-4 rounded-[var(--rs)] border px-3.5 py-2.5 text-left text-[12px]",
              banner.kind === "err" && "border-status-failed/40 bg-status-failed/10 text-status-failed",
              banner.kind === "info" && "border-border bg-canvas text-text-muted"
            )}
          >
            {banner.text}
          </div>
        )}

        <a href={googleLoginUrl("/")} className="block">
          <Button className="w-full bg-role-supervisor text-background hover:bg-role-supervisor/90">
            Sign in with Google
          </Button>
        </a>
      </div>
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={<div className="h-screen bg-canvas" />}>
      <LoginContent />
    </Suspense>
  );
}
