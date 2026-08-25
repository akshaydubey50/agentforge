"use client";

import { useEffect, useState, type ComponentType } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import useSWR from "swr";
import {
  Activity,
  BarChart3,
  BookOpen,
  Brain,
  Database,
  FileCheck2,
  GitBranch,
  Home,
  KeyRound,
  Link2,
  MemoryStick,
  Plug,
  SearchCheck,
  Settings,
  Shield,
  SlidersHorizontal,
  Wrench,
} from "lucide-react";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

export const LAST_TASK_STORAGE_KEY = "agentforge:last-task-id";
const COLLAPSED_STORAGE_KEY = "agentforge:nav-collapsed";

type Tone = "default" | "attention";

interface NavItem {
  href: string;
  icon: ComponentType<{ className?: string }>;
  label: string;
  badge?: string | number | null;
  tone?: Tone;
  match?: (pathname: string) => boolean;
}

function NavRow({ item, collapsed, active }: { item: NavItem; collapsed: boolean; active: boolean }) {
  const Icon = item.icon;
  const badge =
    item.badge !== null && item.badge !== undefined && item.badge !== 0 && item.badge !== "" ? item.badge : null;

  const content = (
    <Link
      href={item.href}
      className={cn(
        "flex items-center gap-2.5 rounded-[8px] text-[13px] transition-colors",
        collapsed ? "h-[38px] w-[38px] justify-center px-0 py-0" : "px-2.5 py-[7px]",
        active ? "bg-surface-3 text-text" : "text-text-muted hover:bg-surface-3/60 hover:text-text"
      )}
      aria-label={item.label}
    >
      <Icon className={cn("h-4 w-4 flex-none", active ? "text-role-supervisor" : "text-text-faint")} />
      {!collapsed && <span className="min-w-0 flex-1 truncate">{item.label}</span>}
      {!collapsed && badge !== null && (
        <span
          className={cn(
            "flex-none rounded-full px-1.5 py-0.5 text-[10.5px] font-medium tabular-nums",
            item.tone === "attention" ? "bg-role-human/15 text-role-human" : "text-text-faint"
          )}
        >
          {badge}
        </span>
      )}
      {collapsed && badge !== null && (
        <span
          className={cn(
            "absolute right-[5px] top-[5px] h-[7px] w-[7px] rounded-full",
            item.tone === "attention" ? "bg-role-human" : "bg-text-faint"
          )}
        />
      )}
    </Link>
  );

  if (!collapsed) return content;

  return (
    <Tooltip>
      <TooltipTrigger asChild>{content}</TooltipTrigger>
      <TooltipContent side="right">
        {item.label}
        {badge !== null && ` - ${badge}`}
      </TooltipContent>
    </Tooltip>
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
        // eslint-disable-next-line @next/next/no-img-element -- Google-hosted avatar.
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
          <button onClick={logout} disabled={loggingOut} className="transition-opacity hover:opacity-80" aria-label="Sign out">
            {avatar}
          </button>
        </TooltipTrigger>
        <TooltipContent side="right">{user.name || user.email} - sign out</TooltipContent>
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
  const [lastTaskId, setLastTaskId] = useState<string | null>(null);

  const { data: summary } = useSWR("system-summary", () => api.getSystemSummary(), {
    refreshInterval: 5000,
  });
  const { data: topology } = useSWR("system-topology", () => api.getSystemTopology(), {
    revalidateOnFocus: false,
  });

  useEffect(() => {
    try {
      setCollapsed(localStorage.getItem(COLLAPSED_STORAGE_KEY) === "1");
    } catch {
      // localStorage unavailable; stay expanded.
    }
  }, []);

  useEffect(() => {
    try {
      setLastTaskId(localStorage.getItem(LAST_TASK_STORAGE_KEY));
    } catch {
      // Workspace just opens without a selected run.
    }
  }, [pathname]);

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

  const workspaceHref = lastTaskId ? `/workspace?run=${lastTaskId}` : "/workspace";
  const model = topology?.subsystems.find((s) => s.id === "models")?.facts[0]?.value;

  const groups: { title: string; items: NavItem[] }[] = [
    {
      title: "Work",
      items: [
        { href: "/mission", icon: Home, label: "Mission Control", match: (p) => p === "/" || p.startsWith("/mission") },
        { href: workspaceHref, icon: GitBranch, label: "Workspace", badge: summary?.tasks.active, match: (p) => p.startsWith("/workspace") },
        { href: "/runs", icon: SearchCheck, label: "Runs", match: (p) => p.startsWith("/runs") },
        { href: "/approvals", icon: FileCheck2, label: "Approvals", badge: summary?.approvals_pending, tone: "attention" },
      ],
    },
    {
      title: "Capabilities",
      items: [
        { href: "/knowledge", icon: BookOpen, label: "Knowledge" },
        { href: "/memory", icon: MemoryStick, label: "Memory", badge: summary?.memory_entries },
        { href: "/tools", icon: Wrench, label: "Tools", badge: summary?.tools_registered },
        { href: "/mcp", icon: Plug, label: "MCP" },
        { href: "/integrations", icon: Link2, label: "Integrations" },
      ],
    },
    {
      title: "Quality",
      items: [
        { href: "/observability", icon: Activity, label: "Observability" },
        { href: "/evals", icon: BarChart3, label: "Evals" },
      ],
    },
    {
      title: "Govern",
      items: [
        { href: "/policies", icon: SlidersHorizontal, label: "Policies" },
        { href: "/security", icon: Shield, label: "Security" },
      ],
    },
    {
      title: "System",
      items: [{ href: "/settings", icon: Settings, label: "Settings" }],
    },
  ];

  const isActive = (item: NavItem) => (item.match ? item.match(pathname) : pathname.startsWith(item.href));

  return (
    <nav
      className={cn(
        "flex flex-none flex-col border-r border-border bg-rail transition-[width]",
        collapsed ? "w-[60px] items-center py-4" : "w-[228px] px-3 py-4"
      )}
    >
      <div className={cn("flex items-center", collapsed ? "mb-3.5 justify-center" : "mb-4 gap-2.5 px-1")}>
        <span className="flex h-[30px] w-[30px] flex-none items-center justify-center rounded-[8px] border border-role-supervisor/45 bg-role-supervisor/15 text-[12px] font-bold text-role-supervisor">
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
                <TooltipContent side="right">Primary model. Reviewer may use a separate tier.</TooltipContent>
              </Tooltip>
            )}
          </span>
        )}
      </div>

      <div className={cn("flex min-h-0 flex-1 flex-col overflow-y-auto", collapsed ? "gap-1.5" : "gap-3.5")}>
        {groups.map((group) => (
          <div key={group.title} className={cn("flex flex-col", collapsed ? "gap-1.5" : "gap-0.5")}>
            {!collapsed && (
              <div className="px-2.5 pb-1 text-[10px] uppercase tracking-[0.11em] text-text-faint">{group.title}</div>
            )}
            {group.items.map((item) => (
              <div key={`${group.title}-${item.label}`} className="relative">
                <NavRow item={item} collapsed={collapsed} active={isActive(item)} />
              </div>
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
            collapsed ? "h-[28px] w-[28px]" : "px-2.5 py-1 text-left"
          )}
        >
          {collapsed ? <KeyRound className="mx-auto h-3.5 w-3.5" /> : "Collapse"}
        </button>
      </div>
    </nav>
  );
}
