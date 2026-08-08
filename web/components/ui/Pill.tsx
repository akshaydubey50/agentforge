import { cn } from "@/lib/utils";

type PillKind = "ok" | "run" | "wn" | "bad" | "off";

const KIND_CLASSES: Record<PillKind, string> = {
  ok: "bg-ok-wash text-ok [&>i]:bg-ok",
  run: "bg-run-wash text-run [&>i]:bg-run",
  wn: "bg-warn-wash text-warn [&>i]:bg-warn",
  bad: "bg-bad-wash text-bad [&>i]:bg-bad",
  off: "bg-surface-3 text-text-faint [&>i]:bg-text-faint",
};

export function Pill({
  kind,
  live,
  children,
}: {
  kind: PillKind;
  live?: boolean;
  children: React.ReactNode;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-[11px] font-bold",
        KIND_CLASSES[kind]
      )}
    >
      <i className={cn("block h-[5px] w-[5px] rounded-full", live && "animate-now-pulse")} />
      {children}
    </span>
  );
}
