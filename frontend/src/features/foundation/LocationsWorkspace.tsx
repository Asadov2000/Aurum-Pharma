import { type ReactNode } from "react";
import { Link } from "@tanstack/react-router";

import { PageHeader } from "@/components/ui";
import { cn } from "@/lib/utils";

type LocationSection = "branches" | "registers";

export function LocationsWorkspaceHeader({
  active,
  actions,
  meta,
}: {
  active: LocationSection;
  actions?: ReactNode;
  meta?: ReactNode;
}): JSX.Element {
  const content = {
    branches: {
      title: "Точки",
      description: "Аптеки, аптечные пункты, адреса, лицензии и реквизиты чеков.",
    },
    registers: {
      title: "Кассы",
      description: "Рабочие места продаж, привязка к точкам и параметры печати чеков.",
    },
  }[active];

  return (
    <div className="space-y-4">
      <PageHeader
        title={content.title}
        description={content.description}
        meta={meta}
        actions={actions}
        showTitleOnDesktop
      />
      <nav
        className="flex min-w-0 gap-1 overflow-x-auto border-b border-border"
        aria-label="Управление торговыми точками"
      >
        <WorkspaceTab to="/branches" active={active === "branches"}>
          Точки
        </WorkspaceTab>
        <WorkspaceTab to="/registers" active={active === "registers"}>
          Кассы
        </WorkspaceTab>
      </nav>
    </div>
  );
}

function WorkspaceTab({
  to,
  active,
  children,
}: {
  to: "/branches" | "/registers";
  active: boolean;
  children: ReactNode;
}): JSX.Element {
  return (
    <Link
      to={to}
      aria-current={active ? "page" : undefined}
      className={cn(
        "flex min-h-11 shrink-0 items-center border-b-2 px-4 text-sm font-semibold transition-colors duration-fast",
        active
          ? "border-primary text-primary"
          : "border-transparent text-foreground-secondary hover:text-foreground",
      )}
    >
      {children}
    </Link>
  );
}

export interface LocationMetric {
  label: string;
  value: number;
  tone?: "default" | "success" | "warning" | "danger";
}

export function LocationsSummary({
  label,
  metrics,
  loading,
}: {
  label: string;
  metrics: readonly LocationMetric[];
  loading: boolean;
}): JSX.Element {
  const toneClass = {
    default: "text-foreground",
    success: "text-success",
    warning: "text-warning-foreground",
    danger: "text-danger",
  };

  return (
    <section
      className="grid grid-cols-2 overflow-hidden rounded-lg border border-border bg-surface xl:grid-cols-4"
      aria-label={label}
    >
      {metrics.map((metric, index) => (
        <div
          key={metric.label}
          className={cn(
            "min-w-0 px-5 py-4",
            index % 2 === 1 && "border-l border-border",
            index >= 2 && "border-t border-border",
            index >= 2 && "xl:border-t-0",
            index === 2 && "xl:border-l",
          )}
        >
          <p className="text-xs font-medium text-foreground-muted">{metric.label}</p>
          {loading ? (
            <div className="mt-2 h-7 w-16 animate-pulse rounded bg-foreground/10" />
          ) : (
            <p
              className={cn(
                "mt-1 font-display text-2xl font-semibold tabular-nums",
                toneClass[metric.tone ?? "default"],
              )}
            >
              {metric.value.toLocaleString("ru-RU")}
            </p>
          )}
        </div>
      ))}
    </section>
  );
}
