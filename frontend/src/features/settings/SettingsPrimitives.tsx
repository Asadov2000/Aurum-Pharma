import { type ReactNode } from "react";

import { Badge } from "@/components/ui";
import { cn } from "@/lib/utils";

export function SettingsSectionHeader({
  title,
  description,
  ownerOnly = false,
  deviceOnly = false,
  trailing,
}: {
  title: string;
  description?: string;
  ownerOnly?: boolean;
  deviceOnly?: boolean;
  trailing?: ReactNode;
}): JSX.Element {
  return (
    <div className="flex flex-col gap-3 border-b border-border pb-4 sm:flex-row sm:items-start sm:justify-between">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="font-display text-xl font-semibold text-foreground">{title}</h2>
          {ownerOnly ? <Badge tone="neutral">Только владельцу</Badge> : null}
          {deviceOnly ? <Badge tone="neutral">Только на этом устройстве</Badge> : null}
        </div>
        {description ? (
          <p className="mt-1 max-w-3xl text-sm leading-5 text-foreground-muted">{description}</p>
        ) : null}
      </div>
      {trailing ? <div className="shrink-0">{trailing}</div> : null}
    </div>
  );
}

export function SettingRow({
  title,
  description,
  children,
  className,
}: {
  title: string;
  description?: string;
  children: ReactNode;
  className?: string;
}): JSX.Element {
  return (
    <div
      className={cn(
        "grid gap-3 border-b border-border py-4 last:border-b-0 md:grid-cols-[minmax(12rem,1fr)_minmax(18rem,1.25fr)] md:items-center",
        className,
      )}
    >
      <div className="min-w-0">
        <p className="text-sm font-medium text-foreground">{title}</p>
        {description ? (
          <p className="mt-1 text-xs leading-5 text-foreground-muted">{description}</p>
        ) : null}
      </div>
      <div className="min-w-0 md:justify-self-end">{children}</div>
    </div>
  );
}

export function SettingsNotice({
  tone = "info",
  children,
}: {
  tone?: "info" | "warning" | "danger" | "success";
  children: ReactNode;
}): JSX.Element {
  const toneClass = {
    info: "border-info/25 bg-info-subtle text-info-foreground",
    warning: "border-warning/25 bg-warning-subtle text-warning-foreground",
    danger: "border-danger/25 bg-danger-subtle text-danger-foreground",
    success: "border-success/25 bg-success-subtle text-success-foreground",
  }[tone];
  return (
    <div
      role={tone === "danger" ? "alert" : "status"}
      className={cn("rounded-md border px-3 py-2 text-sm", toneClass)}
    >
      {children}
    </div>
  );
}
