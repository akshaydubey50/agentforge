import { TopBar } from "@/components/shell/TopBar";

function Row({
  title,
  description,
  control,
}: {
  title: string;
  description: string;
  control: React.ReactNode;
}) {
  return (
    <div className="flex items-start gap-3.5 border-b border-border py-3.5 last:border-b-0">
      <div className="flex-1">
        <div className="font-semibold text-text">{title}</div>
        <div className="max-w-[56ch] text-[12px] text-text-muted">{description}</div>
      </div>
      {control}
    </div>
  );
}

export default function SettingsPage() {
  return (
    <>
      <TopBar title="Settings" />
      <div className="flex-1 overflow-y-auto px-5 py-5">
        <p className="mb-4 text-[12.5px] text-text-faint">
          None of the controls below are wired up yet — there&apos;s no autonomy/budget/team model
          in the backend. This is a preview of the intended layout, not a working settings page.
        </p>

        <h3 className="mb-2.5 mt-0 text-[11px] font-extrabold uppercase tracking-wide text-text-faint">
          How much freedom
        </h3>
        <div className="rounded-[var(--rm)] border border-dashed border-border-strong bg-surface px-4">
          <Row
            title="Autonomy"
            description="How far it goes before checking with you."
            control={
              <div className="flex overflow-hidden rounded-[var(--rs)] border border-border-strong text-[11.5px] font-semibold text-text-muted">
                <button disabled className="border-r border-border px-2.5 py-1.5">
                  Suggest only
                </button>
                <button disabled className="bg-brand px-2.5 py-1.5 text-brand-foreground">
                  Ask first
                </button>
                <button disabled className="px-2.5 py-1.5">
                  Just do it
                </button>
              </div>
            }
          />
          <Row
            title="Monthly budget"
            description="Work stops when this is reached."
            control={
              <button disabled className="rounded-[var(--rs)] border border-border-strong px-2.5 py-1 text-[11.5px] font-semibold text-text-muted">
                $10.00
              </button>
            }
          />
        </div>

        <h3 className="mb-2.5 mt-4 text-[11px] font-extrabold uppercase tracking-wide text-text-faint">Team</h3>
        <div className="rounded-[var(--rm)] border border-dashed border-border-strong bg-surface px-4">
          <Row
            title="Invite someone"
            description="No multi-tenancy yet — the API is open to anyone who can reach it."
            control={
              <button disabled className="rounded-[var(--rs)] border border-border-strong px-2.5 py-1 text-[11.5px] font-semibold text-text-muted">
                Invite
              </button>
            }
          />
        </div>
      </div>
    </>
  );
}
