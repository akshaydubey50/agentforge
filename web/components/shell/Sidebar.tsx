"use client";

// The nav is grouped by what you are trying to do, not by feature list:
//
//   WORK    submit and steer tasks          (the operator's surface)
//   SYSTEM  understand what it did and why  (the builder's surface)
//   SETUP   connect and configure it
//
// Counts live in the nav itself, so system state is readable before clicking
// anything. They come from one shared /v1/system/summary poll -- the System
// page uses the same SWR key, so the two surfaces cost a single request
// between them rather than one each.
//
// Collapsing falls back to the original 60px icon rail, so the compact
// layout is still there for anyone who preferred it; it is now a state
// rather than the only option.

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import useSWR from "swr";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

export const LAST_TASK_STORAGE_KEY = "agentforge:last-task-id";
const COLLAPSED_STORAGE_KEY = "agentforge:nav-collapsed";

type Tone = "default" | "attention";

interface NavItem {
  href: string;
  glyph: string;
  label: string;
  badge?: string | number | null;
  tone?: Tone;
  /** Match nested routes (/tasks/abc) as well as the exact path. */
  match?: (pathname: string) => boolean;
}

function NavRow({ item, collapsed, active }: { item: NavItem; collapsed: boolean; active: boolean }) {
  const badge =
    item.badge !== null && item.badge !== undefined && item.badge !== 0 && item.badge !== "" ? item.badge : null;

  if (collapsed) {
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <Link
            href={item.href}
            className={cn(
              "relative flex h-[38px] w-[38px] flex-none items-center justify-center rounded-[9px] text-[16px] text-text-faint transition-colors",
              "hover:bg-surface-3 hover:text-text",
              active && "bg-surface-3 text-role-supervisor hover:text-role-supervisor"
            )}
          >
            {item.glyph}
            {badge !== null && (
              <span
                className={cn(
                  "absolute right-[5px] top-[5px] h-[7px] w-[7px] rounded-full",
                  item.tone === "attention" ? "bg-role-human" : "bg-text-faint"
                )}
              />
            )}
          </Link>
        </TooltipTrigger>
        <TooltipContent side="right">
          {item.label}
          {badge !== null && ` — ${badge}`}
        </TooltipContent>
      </Tooltip>
    );
  }

  return (
    <Link
      href={item.href}
      className={cn(
        "flex items-center gap-2.5 rounded-[8px] px-2.5 py-[7px] text-[13px] transition-colors",
        active
          ? "bg-surface-3 text-text"
          : "text-text-muted hover:bg-surface-3/60 hover:text-text"
      )}
    >
      <span className={cn("w-[15px] flex-none text-[13px]", active ? "text-role-supervisor" : "text-text-faint")}>
        {item.glyph}
      </span>
      <span className="min-w-0 flex-1 truncate">{item.label}</span>
      {badge !== null && (
        <span
          className={cn(
            "flex-none rounded-full px-1.5 py-0.5 text-[10.5px] font-medium tabular-nums",
            item.tone === "attention" ? "bg-role-human/15 text-role-human" : "text-text-faint"
          )}
        >
          {badge}
        </span>
      )}
    </Link>
  );
}

function UserRailIcon({ collapsed }: { collapsed: boolean }) {
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

  const avatar = (
    <span className="flex h-[28px] w-[28px] flex-none items-center justify-center overflow-hidden rounded-full bg-surface-3 text-[12px] font-semibold text-text-muted">
      {user.picture_url ? (
        // eslint-disable-next-line @next/next/no-img-element -- an external
        // Google-hosted avatar, not a local/optimizable asset.
        <img src={user.picture_url} alt="" className="h-full w-full object-cover" />
      ) : (
        initial
      )}
    </span>
  );

  if (collapsed) {
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <button onClick={logout} disabled={loggingOut} className="transition-opacity hover:opacity-80">
            {avatar}
          </button>
        </TooltipTrigger>
        <TooltipContent side="right">{user.name || user.email} — sign out</TooltipContent>
      </Tooltip>
    );
  }

  return (
    <button
      onClick={logout}
      disabled={loggingOut}
      className="flex w-full items-center gap-2.5 rounded-[8px] px-2 py-1.5 text-left transition-colors hover:bg-surface-3/60"
    >
      {avatar}
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[12.5px] text-text">{user.name || user.email}</span>
        <span className="block text-[11px] text-text-faint">Sign out</span>
      </span>
    </button>
  );
}

export function Sidebar() {
  const pathname = usePathname();
  const [collapsed, setCollapsed] = useState(false);

  const { data: summary } = useSWR("system-summary", () => api.getSystemSummary(), {
    refreshInterval: 5000,
  });
  // Static per deployment -- fetched here only for the model chip under the
  // wordmark, which answers "which brain is this?" without a trip to Settings.
  const { data: topology } = useSWR("system-topology", () => api.getSystemTopology(), {
    revalidateOnFocus: false,
  });

  useEffect(() => {
    try {
      setCollapsed(localStorage.getItem(COLLAPSED_STORAGE_KEY) === "1");
    } catch {
      // localStorage unavailable -- stay expanded
    }
  }, []);

  const toggle = () => {
    setCollapsed((value) => {
      const next = !value;
      try {
        localStorage.setItem(COLLAPSED_STORAGE_KEY, next ? "1" : "0");
      } catch {
        // ignore
      }
      return next;
    });
  };

  // The Agent graph needs a task to point at when the user isn't already
  // looking at one -- task detail pages write their id here on mount, so the
  // nav can jump back into whichever task was last open instead of
  // dead-ending at nothing.
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

  const model = topology?.subsystems.find((s) => s.id === "models")?.facts[0]?.value;

  const groups: { title: string; items: NavItem[] }[] = [
    {
      title: "Work",
      items: [
        { href: "/ask", glyph: "✎", label: "Ask" },
        {
          href: "/tasks",
          glyph: "▤",
          label: "Tasks",
          badge: summary?.tasks.active,
          match: (p) => p.startsWith("/tasks") && !p.endsWith("/graph"),
        },
        {
          href: "/approvals",
          glyph: "!",
          label: "Approvals",
          badge: summary?.approvals_pending,
          tone: "attention",
        },
        { href: "/knowledge", glyph: "◫", label: "Knowledge" },
      ],
    },
    {
      title: "System",
      items: [
        { href: "/system", glyph: "◉", label: "Overview" },
        { href: "/runs", glyph: "≡", label: "Runs" },
        {
          href: graphHref,
          glyph: "◈",
          label: "Agent graph",
          match: (p) => p.startsWith("/tasks") && p.endsWith("/graph"),
        },
        { href: "/memory", glyph: "⬡", label: "Memory", badge: summary?.memory_entries },
        { href: "/tools", glyph: "⚒", label: "Tools", badge: summary?.tools_registered },
        { href: "/analytics", glyph: "▨", label: "Analytics" },
        {
          href: "/usage",
          glyph: "$",
          label: "Usage",
          badge: summary ? `$${summary.spend.usd.toFixed(2)}` : null,
        },
      ],
    },
    {
      title: "Setup",
      items: [
        { href: "/integrations", glyph: "⧉", label: "Integrations" },
        { href: "/settings", glyph: "⚙", label: "Settings" },
      ],
    },
  ];

  const isActive = (item: NavItem) =>
    item.match ? item.match(pathname) : pathname.startsWith(item.href);

  return (
    <nav
      className={cn(
        "flex flex-none flex-col border-r border-border bg-rail transition-[width]",
        collapsed ? "w-[60px] items-center py-4" : "w-[212px] px-3 py-4"
      )}
    >
      <div className={cn("flex items-center", collapsed ? "mb-3.5 justify-center" : "mb-4 gap-2.5 px-1")}>
        <span className="flex h-[30px] w-[30px] flex-none items-center justify-center rounded-[9px] bg-gradient-to-br from-role-supervisor to-[#6D5AE0] text-[12px] font-bold text-background">
          AF
        </span>
        {!collapsed && (
          <span className="min-w-0 flex-1">
            <span className="block truncate text-[13.5px] font-semibold text-text">AgentForge</span>
            {model && (
              <Tooltip>
                <TooltipTrigger asChild>
                  <span className="block truncate font-mono text-[10.5px] text-text-faint">{model}</span>
                </TooltipTrigger>
                <TooltipContent side="right">Agent model — the reviewer runs on a separate tier</TooltipContent>
              </Tooltip>
            )}
          </span>
        )}
      </div>

      <div className={cn("flex min-h-0 flex-1 flex-col overflow-y-auto", collapsed ? "gap-1.5" : "gap-3.5")}>
        {groups.map((group) => (
          <div key={group.title} className={cn("flex flex-col", collapsed ? "gap-1.5" : "gap-0.5")}>
            {!collapsed && (
              <div className="px-2.5 pb-1 text-[10px] uppercase tracking-[0.11em] text-text-faint">
                {group.title}
              </div>
            )}
            {group.items.map((item) => (
              <NavRow key={item.label} item={item} collapsed={collapsed} active={isActive(item)} />
            ))}
          </div>
        ))}
      </div>

      <div className={cn("mt-3 flex flex-col gap-2 border-t border-border pt-3", collapsed && "items-center")}>
        <UserRailIcon collapsed={collapsed} />
        <button
          onClick={toggle}
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          className={cn(
            "rounded-[7px] text-[12px] text-text-faint transition-colors hover:bg-surface-3/60 hover:text-text",
            collapsed ? "h-[26px] w-[26px]" : "px-2.5 py-1 text-left"
          )}
        >
          {collapsed ? "›" : "‹ Collapse"}
        </button>
      </div>
    </nav>
  );
}
