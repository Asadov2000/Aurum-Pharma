import { useDeferredValue, useEffect, useMemo, useState } from "react";
import { useMutationState } from "@tanstack/react-query";

import { Input, Select } from "@/components/ui";
import { useAuth } from "@/features/auth/hooks";
import { SecurityPage } from "@/features/auth/SecurityPage";
import { SubscriptionsForm } from "@/features/notifications/SubscriptionsForm";
import { cn } from "@/lib/utils";

import { AccountSettingsPanel, MenuSettingsPanel } from "./AccountAndMenuPanels";
import { DeviceSettingsPanel } from "./DeviceSettingsPanel";
import { InterfaceSettingsPanel } from "./InterfaceSettingsPanel";
import { OwnerSettingsPanel, type OwnerSettingsSection } from "./OwnerSettingsPanel";
import {
  parseSettingsSearch,
  settingsCategories,
  settingsCategoryGroups,
  settingsGroupLabels,
  type SettingsSectionId,
} from "./search";
import { SettingsSectionHeader } from "./SettingsPrimitives";
import { settingsKeys, useUserPreferencesQuery } from "./queries";

type CategoryId = SettingsSectionId | OwnerSettingsSection;

export function SettingsPage(): JSX.Element {
  const { user } = useAuth();
  const preferences = useUserPreferencesQuery();
  const preferenceMutationStatuses = useMutationState({
    filters: { mutationKey: settingsKeys.preferencesUpdate },
    select: (mutation) => mutation.state.status,
  });
  const [active, setActive] = useState<CategoryId>(() => {
    if (typeof window === "undefined") return "interface";
    const search = Object.fromEntries(new URLSearchParams(window.location.search));
    return parseSettingsSearch(search).section ?? "interface";
  });
  const [search, setSearch] = useState("");
  const deferredSearch = useDeferredValue(search.trim().toLocaleLowerCase("ru"));
  const canManagePharmacySettings = Boolean(
    user?.is_tenant_owner &&
    !user.is_developer &&
    !user.is_administrator &&
    user.support_access == null,
  );
  const available = useMemo(
    () =>
      settingsCategories.filter(
        (category) => category.group !== "owner" || canManagePharmacySettings,
      ),
    [canManagePharmacySettings],
  );
  const filtered = useMemo(
    () =>
      deferredSearch.length === 0
        ? available
        : available.filter((category) =>
            `${category.title} ${category.description} ${category.keywords}`
              .toLocaleLowerCase("ru")
              .includes(deferredSearch),
          ),
    [available, deferredSearch],
  );

  useEffect(() => {
    if (!available.some((category) => category.id === active)) setActive("interface");
  }, [active, available]);

  const lastMutationStatus = preferenceMutationStatuses.at(-1);
  const syncError = Boolean(preferences.error) || lastMutationStatus === "error";
  const syncPending = preferences.isFetching || lastMutationStatus === "pending";
  const syncLabel = syncError
    ? "Только на этом устройстве"
    : syncPending
      ? "Синхронизация…"
      : "Синхронизировано";

  return (
    <div className="mx-auto w-full max-w-[92rem] space-y-4" data-testid="settings-page">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="font-display text-2xl font-semibold text-foreground lg:sr-only">
            Настройки
          </h1>
          <p className="text-sm text-foreground-muted">
            Личные предпочтения, это устройство и правила аптеки.
          </p>
        </div>
        <span
          className={cn(
            "inline-flex items-center gap-2 text-xs font-medium",
            syncError ? "text-warning-foreground" : "text-success-foreground",
          )}
        >
          <span
            aria-hidden="true"
            className={cn("h-2 w-2 rounded-full", syncError ? "bg-warning" : "bg-success")}
          />
          {syncLabel}
        </span>
      </div>

      <div className="relative">
        <span
          aria-hidden="true"
          className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-foreground-muted"
        >
          <SearchIcon />
        </span>
        <Input
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Найти настройку"
          aria-label="Найти настройку"
          className="pl-10"
        />
      </div>

      <div className="xl:hidden">
        <Select
          aria-label="Раздел настроек"
          value={
            filtered.some((category) => category.id === active) ? active : (filtered[0]?.id ?? "")
          }
          disabled={filtered.length === 0}
          onChange={(event) => setActive(event.target.value as CategoryId)}
        >
          {filtered.length === 0 ? <option value="">Настройки не найдены</option> : null}
          {filtered.map((category) => (
            <option key={category.id} value={category.id}>
              {category.title}
            </option>
          ))}
        </Select>
      </div>

      <div className="overflow-hidden rounded-md border border-border bg-surface xl:grid xl:grid-cols-[20rem_minmax(0,1fr)]">
        <nav aria-label="Разделы настроек" className="hidden border-r border-border p-3 xl:block">
          {filtered.length === 0 ? (
            <p className="px-3 py-6 text-sm text-foreground-muted">Настройки не найдены.</p>
          ) : (
            settingsCategoryGroups.map((group) => {
              const items = filtered.filter((category) => category.group === group);
              if (items.length === 0) return null;
              return (
                <div key={group} className="mb-4 last:mb-0">
                  <p className="mb-1 px-3 text-xs font-semibold uppercase text-foreground-muted">
                    {settingsGroupLabels[group]}
                  </p>
                  <div className="space-y-1">
                    {items.map((category) => (
                      <button
                        key={category.id}
                        type="button"
                        aria-current={active === category.id ? "page" : undefined}
                        onClick={() => setActive(category.id)}
                        className={cn(
                          "grid min-h-12 w-full grid-cols-[1.5rem_minmax(0,1fr)] items-center gap-3 rounded-md border-l-2 px-3 py-2 text-left",
                          active === category.id
                            ? "border-primary bg-primary/[0.08] text-primary"
                            : "border-transparent text-foreground-secondary hover:bg-foreground/[0.04] hover:text-foreground",
                        )}
                      >
                        <CategoryIcon category={category.id} />
                        <span className="min-w-0">
                          <span className="block truncate text-sm font-medium">
                            {category.title}
                          </span>
                          <span className="block truncate text-xs text-foreground-muted">
                            {category.description}
                          </span>
                        </span>
                      </button>
                    ))}
                  </div>
                </div>
              );
            })
          )}
        </nav>

        <section className="min-h-[36rem] min-w-0 p-4 sm:p-6" aria-live="polite">
          {active === "account" ? <AccountSettingsPanel /> : null}
          {active === "interface" ? <InterfaceSettingsPanel /> : null}
          {active === "menu" ? <MenuSettingsPanel /> : null}
          {active === "notifications" ? (
            <div className="space-y-4">
              <SettingsSectionHeader
                title="Уведомления"
                description="Какие события будут приходить в центр уведомлений."
              />
              <SubscriptionsForm />
            </div>
          ) : null}
          {active === "security" ? <SecurityPage embedded /> : null}
          {active === "device" ? <DeviceSettingsPanel /> : null}
          {active === "pharmacy" ||
          active === "sales" ||
          active === "inventory" ||
          active === "reports" ? (
            <OwnerSettingsPanel section={active} />
          ) : null}
        </section>
      </div>
    </div>
  );
}

function SearchIcon(): JSX.Element {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
    >
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-4-4" />
    </svg>
  );
}

function CategoryIcon({ category }: { category: CategoryId }): JSX.Element {
  const paths: Record<CategoryId, JSX.Element> = {
    account: (
      <>
        <circle cx="12" cy="8" r="3" />
        <path d="M6 20c0-4 2.7-6 6-6s6 2 6 6" />
      </>
    ),
    interface: (
      <>
        <rect x="3" y="4" width="18" height="13" rx="1" />
        <path d="M8 21h8M12 17v4" />
      </>
    ),
    menu: (
      <>
        <rect x="4" y="4" width="6" height="6" />
        <rect x="14" y="4" width="6" height="6" />
        <rect x="4" y="14" width="6" height="6" />
        <rect x="14" y="14" width="6" height="6" />
      </>
    ),
    notifications: (
      <>
        <path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9" />
        <path d="M10 21h4" />
      </>
    ),
    security: <path d="M12 3 5 6v5c0 5 3 8 7 10 4-2 7-5 7-10V6l-7-3Z" />,
    device: (
      <>
        <rect x="3" y="5" width="18" height="14" rx="2" />
        <path d="M8 9h8M7 15h10" />
      </>
    ),
    pharmacy: (
      <>
        <path d="M4 9h16v11H4zM3 9l2-5h14l2 5" />
        <path d="M10 14h4M12 12v4" />
      </>
    ),
    sales: (
      <>
        <path d="M5 12h14M8 8l-4 4 4 4M16 8l4 4-4 4" />
      </>
    ),
    inventory: (
      <>
        <path d="M4 7h16v13H4zM8 4h8l2 3H6l2-3" />
        <path d="M9 12h6" />
      </>
    ),
    reports: (
      <>
        <circle cx="12" cy="12" r="9" />
        <path d="M12 7v5l3 2" />
      </>
    ),
  };
  return (
    <svg
      aria-hidden="true"
      width="20"
      height="20"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {paths[category]}
    </svg>
  );
}
