import { PLATFORM_CAPABILITIES } from "@/features/auth/platformCapabilities";

import {
  type PlatformAccessKind,
  type PlatformAccessReasonCode,
  type PlatformAccessStatus,
} from "./types";

export const accessKindLabel: Record<PlatformAccessKind, string> = {
  developer: "Разработчик",
  administrator: "Администратор",
};

export const accessStatusLabel: Record<PlatformAccessStatus, string> = {
  pending: "Ожидает подтверждения",
  active: "Активен",
  revoked: "Отозван",
  expired: "Истёк",
};

export const accessStatusTone: Record<
  PlatformAccessStatus,
  "warning" | "success" | "danger" | "neutral"
> = {
  pending: "warning",
  active: "success",
  revoked: "danger",
  expired: "neutral",
};

export const accessReasonLabel: Record<PlatformAccessReasonCode, string> = {
  platform_staff_onboarding: "Подключение сотрудника платформы",
  responsibility_change: "Изменение обязанностей",
  security_incident: "Инцидент безопасности",
  access_review: "Проверка доступов",
  other: "Другая причина",
};

export const platformCapabilityLabel: Record<string, string> = {
  [PLATFORM_CAPABILITIES.tenantsView]: "Просмотр аптек",
  [PLATFORM_CAPABILITIES.tenantsManage]: "Управление аптеками",
  [PLATFORM_CAPABILITIES.membershipsManage]: "Управление сотрудниками",
  [PLATFORM_CAPABILITIES.ownershipProvision]: "Назначение владельцев",
  [PLATFORM_CAPABILITIES.billingManage]: "Управление биллингом",
  [PLATFORM_CAPABILITIES.billingView]: "Просмотр расчётов",
  "platform.billing.payment.review": "Проверка заявлений об оплате",
  "platform.billing.payment.approve": "Подтверждение оплаты",
  "platform.billing.invoice.issue": "Выпуск счетов",
  "platform.billing.adjustment.create": "Создание корректировок",
  "platform.billing.adjustment.approve": "Утверждение корректировок",
  "platform.billing.plan.manage": "Управление тарифами",
  "platform.billing.audit.view": "Аудит расчётов",
  [PLATFORM_CAPABILITIES.supportUse]: "Защищённая поддержка",
  [PLATFORM_CAPABILITIES.syncView]: "Просмотр синхронизации",
  [PLATFORM_CAPABILITIES.syncManage]: "Управление синхронизацией",
  [PLATFORM_CAPABILITIES.auditGlobalView]: "Глобальный аудит",
  [PLATFORM_CAPABILITIES.accessView]: "Просмотр доступов платформы",
  [PLATFORM_CAPABILITIES.accessManage]: "Управление доступами платформы",
  [PLATFORM_CAPABILITIES.accountsView]: "Просмотр команды Aurum",
  [PLATFORM_CAPABILITIES.accountsManage]: "Приглашение команды Aurum",
};
