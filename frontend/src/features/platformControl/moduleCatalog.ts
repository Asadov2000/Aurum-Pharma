import {
  PLATFORM_CAPABILITIES,
  type PlatformCapability,
} from "@/features/auth/platformCapabilities";

export interface PlatformModule {
  id: "tenants" | "audit";
  title: string;
  description: string;
  to: "/admin/tenants" | "/audit";
  capability: PlatformCapability;
  tone: "primary" | "neutral";
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
    id: "audit",
    title: "Глобальный аудит",
    description: "Контроль критических действий и событий платформы",
    to: "/audit",
    capability: PLATFORM_CAPABILITIES.auditGlobalView,
    tone: "neutral",
  },
] as const;

export function availablePlatformModules(capabilities: readonly string[]): PlatformModule[] {
  return MODULES.filter((module) => capabilities.includes(module.capability));
}
