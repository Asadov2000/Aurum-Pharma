import { type AppRoutePath } from "@/components/layout/routeAccess";

import { type ReadinessStep, type ReadinessStepCode, type ReadinessTaskCode } from "./types";

export interface ReadinessStepDefinition {
  code: ReadinessStepCode;
  title: string;
  description: string;
  to?: AppRoutePath;
  actionLabel?: string;
}

export const readinessSteps: ReadinessStepDefinition[] = [
  {
    code: "pharmacy_profile",
    title: "Профиль аптеки",
    description: "Название и контактный email сохранены.",
    to: "/settings",
    actionLabel: "Заполнить профиль",
  },
  {
    code: "licensed_branch",
    title: "Точка и лицензия",
    description: "Нужна активная точка с адресом и действующей лицензией.",
    to: "/branches",
    actionLabel: "Настроить точку",
  },
  {
    code: "receipt_details",
    title: "Реквизиты для чека",
    description: "Укажите название организации и данные, которые печатаются на чеке.",
    to: "/branches",
    actionLabel: "Заполнить реквизиты",
  },
  {
    code: "tenant_owner",
    title: "Владелец аптеки",
    description: "Активный владелец подтверждён и может управлять настройкой.",
  },
  {
    code: "catalog",
    title: "Каталог товаров",
    description: "Добавьте не меньше 100 активных товаров для реальной работы кассы.",
    to: "/catalog",
    actionLabel: "Заполнить каталог",
  },
  {
    code: "pos_settings",
    title: "Настройки кассы",
    description: "Создайте активную кассу. Способы оплаты задаются в настройках аптеки.",
    to: "/registers",
    actionLabel: "Настроить кассу",
  },
  {
    code: "regulatory",
    title: "Правила отпуска",
    description: "Настроены предупреждения по срокам годности, возвратам и рецептам.",
    to: "/settings",
    actionLabel: "Проверить правила",
  },
  {
    code: "ready",
    title: "Готовность к работе",
    description: "Все обязательные требования проверены системой.",
  },
];

export function readinessStepAction(
  definition: ReadinessStepDefinition,
  step: ReadinessStep,
): { to: AppRoutePath; label: string } | null {
  if (!definition.to || !definition.actionLabel) return null;
  if (definition.code === "pos_settings") {
    if (step.action_hint === "payment_methods_missing") {
      return { to: "/settings", label: "Настроить оплату" };
    }
    if (step.action_hint === "operational_branch_missing") {
      return { to: "/registers", label: "Связать кассу с точкой" };
    }
  }
  return { to: definition.to, label: definition.actionLabel };
}

export const taskLabel: Record<ReadinessTaskCode, string> = {
  catalog_loaded: "Каталог загружен (≥100 товаров)",
  first_incoming: "Создан первый приход",
  first_sale: "Первая тестовая продажа",
  second_user: "Приглашён второй сотрудник",
  shift_opened: "Открыта смена",
  test_receipt_printed: "Распечатан тестовый чек",
};
