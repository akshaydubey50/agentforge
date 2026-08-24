"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import useSWR from "swr";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

export const LAST_TASK_STORAGE_KEY = "agentforge:last-task-id";

function RailIcon({
  href,
  glyph,
  label,
  active,
  badge,
}: {
  href: string;
  glyph: string;
  label: string;
  active: boolean;
  badge?: boolean;
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Link
          href={href}
          className={cn(
            "relative flex h-[38px] w-[38px] flex-none items-center justify-center rounded-[9px] text-[16px] text-text-faint transition-colors",
            "hover:bg-surface-3 hover:text-text",
            active && "bg-surface-3 text-role-supervisor hover:text-role-supervisor"
          )}
        >
          {glyph}
          {badge && <span className="absolute right-[5px] top-[5px] h-[7px] w-[7px] rounded-full bg-role-human" />}
        </Link>
      </TooltipTrigger>
      <TooltipContent side="right">{label}</TooltipContent>
    </Tooltip>
  );
}

function UserRailIcon() {
  const { data: user } = useSWR("current-user", () => api.getMe());
  const [loggingOut, setLoggingOut] = useState(false);

  const logout = async () => {
    setLoggingOut(true);
    try {
      await api.logout();
    } finally {
      window.location.href = "/login";
    }
  };

  if (!user) return null;
  const initial = (user.name || user.email)[0]?.toUpperCase();

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          onClick={logout}
          disabled={loggingOut}
          className="relative flex h-[34px] w-[34px] flex-none items-center justify-center overflow-hidden rounded-full bg-surface-3 text-[12.5px] font-semibold text-text-muted transition-opacity hover:opacity-80"
        >
          {user.picture_url ? (
            // eslint-disable-next-line @next/next/no-img-element -- an
            // external Google-hosted avatar, not a local/optimizable asset.
            <img src={user.picture_url} alt="" className="h-full w-full object-cover" />
          ) : (
            initial
          )}
        </button>
      </TooltipTrigger>
      <TooltipContent side="right">{user.name || user.email} — sign out</TooltipContent>
    </Tooltip>
  );
}

export function Sidebar() {
  const pathname = usePathname();
  const { data: pendingCount } = useSWR("pending-escalations-count", () => api.countPendingEscalations(), {
    refreshInterval: 5000,
  });

  // The rail's Graph icon needs a task to point at when the user isn't
  // already looking at one -- task detail pages write their id here on
  // mount (see TaskFeedPage/AgentGraphPage), so the rail can jump back into
  // whichever task was last open instead of dead-ending at nothing.
  const [lastTaskId, setLastTaskId] = useState<string | null>(null);
  useEffect(() => {
    try {
      setLastTaskId(localStorage.getItem(LAST_TASK_STORAGE_KEY));
    } catch {
      // localStorage unavailable -- Graph just falls back to the task list
    }
  }, [pathname]);

  const taskMatch = pathname.match(/^\/tasks\/([^/]+)/);
  const currentTaskId = taskMatch ? taskMatch[1] : null;
  const isGraphRoute = pathname.endsWith("/graph");

  const graphHref = currentTaskId
    ? `/tasks/${currentTaskId}/graph`
    : lastTaskId
      ? `/tasks/${lastTaskId}/graph`
      : "/tasks";

  return (
    <nav className="flex w-[60px] flex-none flex-col items-center gap-1.5 border-r border-border bg-rail py-4">
      <div className="mb-3.5 flex h-[34px] w-[34px] flex-none items-center justify-center rounded-[9px] bg-gradient-to-br from-role-supervisor to-[#6D5AE0] text-[13px] font-bold text-background">
        AF
      </div>

      <RailIcon href="/tasks" glyph="▤" label="Tasks" active={pathname.startsWith("/tasks") && !isGraphRoute} />
      <RailIcon href={graphHref} glyph="◈" label="Agent graph" active={pathname.startsWith("/tasks") && isGraphRoute} />
      <RailIcon
        href="/approvals"
        glyph="!"
        label="Approvals"
        active={pathname.startsWith("/approvals")}
        badge={!!pendingCount}
      />
      <RailIcon href="/knowledge" glyph="◫" label="Knowledge" active={pathname.startsWith("/knowledge")} />
      <RailIcon href="/memory" glyph="⬡" label="Memory" active={pathname.startsWith("/memory")} />
      <RailIcon href="/analytics" glyph="▨" label="Analytics" active={pathname.startsWith("/analytics")} />
      <RailIcon href="/integrations" glyph="⧉" label="Integrations" active={pathname.startsWith("/integrations")} />

      <div className="mt-auto">
        <UserRailIcon />
      </div>
    </nav>
  );
}
