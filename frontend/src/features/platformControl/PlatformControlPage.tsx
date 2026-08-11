import { Link } from "@tanstack/react-router";

import { AccessDeniedCard } from "@/components/AccessDeniedCard";
import { Badge, Card, CardContent, PageHeader } from "@/components/ui";
import { useAuth } from "@/features/auth/hooks";
import { cn } from "@/lib/utils";

import { availablePlatformModules, type PlatformModule } from "./moduleCatalog";

export function PlatformControlPage(): JSX.Element {
  const { user } = useAuth();
  const modules = availablePlatformModules(
    user?.platform_capabilities ?? [],
    user?.is_developer === true,
  );

  if (modules.length === 0) {
    return (
      <AccessDeniedCard
        title="Центр управления"
        message="Для этого аккаунта не назначены инструменты управления платформой."
      />
    );
  }

  return (
    <div className="mx-auto w-full max-w-6xl space-y-5">
      <PageHeader
        title="Центр управления"
        description="Рабочая область Aurum Pharma"
        meta={
          <Badge tone={user?.is_developer ? "info" : "neutral"}>
            {user?.is_developer ? "Разработчик" : "Администратор"}
          </Badge>
        }
      />

      <section aria-label="Доступные разделы">
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          {modules.map((module) => (
            <ModuleLink key={module.id} module={module} />
          ))}
        </div>
      </section>
    </div>
  );
}

function ModuleLink({ module }: { module: PlatformModule }): JSX.Element {
  return (
    <Link
      to={module.to}
      className="group block rounded-lg outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2"
    >
      <Card className="h-full transition-colors group-hover:border-primary/45 group-hover:bg-surface-raised">
        <CardContent className="flex min-h-32 items-center gap-4 sm:min-h-36">
          <span
            aria-hidden="true"
            className={cn(
              "grid h-12 w-12 shrink-0 place-items-center rounded-md",
              module.tone === "primary"
                ? "bg-primary text-primary-foreground"
                : "bg-foreground/[0.07] text-foreground-secondary",
            )}
          >
            <ModuleIcon id={module.id} />
          </span>
          <span className="min-w-0 flex-1">
            <span className="block text-base font-semibold text-foreground">{module.title}</span>
            <span className="mt-1 block text-sm leading-5 text-foreground-muted">
              {module.description}
            </span>
          </span>
          <span
            aria-hidden="true"
            className="text-xl text-foreground-muted group-hover:text-primary"
          >
            →
          </span>
        </CardContent>
      </Card>
    </Link>
  );
}

function ModuleIcon({ id }: { id: PlatformModule["id"] }): JSX.Element {
  return (
    <svg
      width="24"
      height="24"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {id === "tenants" ? (
        <>
          <rect x="3" y="3" width="8" height="18" rx="1" />
          <rect x="13" y="8" width="8" height="13" rx="1" />
          <path d="M6 7h2M6 11h2M6 15h2M16 12h2M16 16h2" />
        </>
      ) : id === "accounts" ? (
        <>
          <circle cx="9" cy="8" r="3" />
          <path d="M3.5 20c.5-4 2.3-6 5.5-6s5 2 5.5 6" />
          <path d="M16 8h5M18.5 5.5v5" />
        </>
      ) : id === "access" ? (
        <>
          <circle cx="12" cy="8" r="3" />
          <path d="M5 20c.6-4 2.9-6 7-6s6.4 2 7 6" />
          <path d="M18 4v4M16 6h4" />
        </>
      ) : (
        <>
          <path d="M9 5h10M9 12h10M9 19h10" />
          <path d="m3 5 1.5 1.5L7 4M3 12l1.5 1.5L7 11M3 19l1.5 1.5L7 18" />
        </>
      )}
    </svg>
  );
}
