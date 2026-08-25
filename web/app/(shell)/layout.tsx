import { AskDock } from "@/components/shell/AskDock";
import { Sidebar } from "@/components/shell/Sidebar";

export default function ShellLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-screen bg-surface">
      <Sidebar />
      <div className="flex h-screen min-w-0 flex-1 flex-col">{children}</div>
      {/* Lives in the shell so the agent is reachable from every page, not
          only from /ask -- see AskDock's header comment. */}
      <AskDock />
    </div>
  );
}
