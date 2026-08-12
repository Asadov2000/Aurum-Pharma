import {
  PLATFORM_CAPABILITIES,
  type PlatformCapability,
} from "@/features/auth/platformCapabilities";

export interface PlatformModule {
  id: "tenants" | "audit" | "access" | "accounts" | "sync";
  title: string;
  description: string;
  to: "/admin/tenants" | "/audit" | "/admin/access" | "/admin/accounts" | "/admin/sync";
  capability: PlatformCapability;
  tone: "primary" | "neutral";
  developerOnly?: boolean;
}

const MODULES: readonly PlatformModule[] = [
  {
    id: "tenants",
    title: "Аптеки",
    description: "Реестр аптек, владельцы, сотрудники, биллинг и защищённая поддержка",
    to: "/admin/tenants",
    capability: PLATFORM_CAPABILITIES.tenantsView,
    tone: "primary",
  },
  {
    id: "accounts",
    title: "Команда Aurum",
    description: "Безопасные приглашения и состояние аккаунтов сотрудников платформы",
    to: "/admin/accounts",
    capability: PLATFORM_CAPABILITIES.accountsView,
    tone: "primary",
  },
  {
    id: "sync",
    title: "Синхронизация",
    description:
      "Связь Edge-узлов, актуальность данных и контроль целостности без доступа к содержимому продаж",
    to: "/admin/sync",
    capability: PLATFORM_CAPABILITIES.syncView,
    tone: "primary",
  },
  {
    id: "access",
    title: "Доступ платформы",
    description: "Проверка, подтверждение и отзыв полномочий команды Aurum Pharma",
    to: "/admin/access",
    capability: PLATFORM_CAPABILITIES.accessView,
    tone: "neutral",
    developerOnly: true,
  },
  {
    id: "audit",
    title: "Глобальный аудит",
    description: "Контроль критических действий и событий платформы",
    to: "/audit",
    capability: PLATFORM_CAPABILITIES.auditGlobalView,
    tone: "neutral",
  },
] as const;

export function availablePlatformModules(
  capabilities: readonly string[],
  isDeveloper = false,
): PlatformModule[] {
  return MODULES.filter(
    (module) => capabilities.includes(module.capability) && (!module.developerOnly || isDeveloper),
  );
}
