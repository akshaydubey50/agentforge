"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import useSWR from "swr";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import { ThemeToggle } from "./ThemeToggle";

const PRIMARY = [
  { href: "/ask", icon: "✳", label: "Ask" },
  { href: "/runs", icon: "◷", label: "Runs" },
];
const CAPABILITIES = [
  { href: "/knowledge", icon: "◫", label: "Knowledge" },
  { href: "/tools", icon: "⚙", label: "Tools" },
  { href: "/automations", icon: "↻", label: "Automations" },
];
const ACCOUNT = [
  { href: "/usage", icon: "◔", label: "Usage" },
  { href: "/settings", icon: "⚑", label: "Settings" },
];

function GroupLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="px-2 pb-1 pt-3 text-[9.5px] font-extrabold uppercase tracking-wider text-text-faint">
      {children}
    </div>
  );
}

function NavLink({
  href,
  icon,
  label,
  active,
  count,
}: {
  href: string;
  icon: string;
  label: string;
  active: boolean;
  count?: number;
}) {
  return (
    <Link
      href={href}
      className={cn(
        "flex items-center gap-2 rounded-[var(--rs)] px-2 py-1.5 text-[13px] font-medium text-text-muted transition-colors",
        "hover:bg-surface-3 hover:text-text",
        active && "bg-brand-wash font-semibold text-brand hover:bg-brand-wash hover:text-brand"
      )}
    >
      <span className="w-4 flex-none text-center">{icon}</span>
      {label}
      {!!count && (
        <span className="ml-auto rounded-full bg-warn px-1.5 text-[10px] font-extrabold text-white">{count}</span>
      )}
    </Link>
  );
}

export function Sidebar() {
  const pathname = usePathname();
  const { data: pendingCount } = useSWR("pending-escalations-count", () => api.countPendingEscalations(), {
    refreshInterval: 5000,
  });

  return (
    <nav className="flex w-[214px] flex-none flex-col gap-0.5 border-r border-border bg-surface-2 p-3">
      <div className="flex items-center gap-2 px-2 pb-4 pt-1">
        <div className="flex h-6 w-6 items-center justify-center rounded-md bg-brand text-[12.5px] font-extrabold text-brand-foreground">
          A
        </div>
        <b className="font-serif-display text-[15px] tracking-tight text-text">AgentForge</b>
      </div>

      {PRIMARY.map((item) => (
        <NavLink key={item.href} {...item} active={pathname.startsWith(item.href)} />
      ))}
      <NavLink
        href="/approvals"
        icon="✓"
        label="Approvals"
        active={pathname.startsWith("/approvals")}
        count={pendingCount}
      />

      <GroupLabel>Capabilities</GroupLabel>
      {CAPABILITIES.map((item) => (
        <NavLink key={item.href} {...item} active={pathname.startsWith(item.href)} />
      ))}

      <GroupLabel>Account</GroupLabel>
      {ACCOUNT.map((item) => (
        <NavLink key={item.href} {...item} active={pathname.startsWith(item.href)} />
      ))}

      <div className="mt-auto border-t border-border pt-2.5 text-[11px] text-text-faint">
        Open dev build · no sign-in yet
        <ThemeToggle />
      </div>
    </nav>
  );
}
