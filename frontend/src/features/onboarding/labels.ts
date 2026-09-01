import { type AppRoutePath } from "@/components/layout/routeAccess";
import { type SettingsSectionId } from "@/features/settings/search";

import { type ReadinessStep, type ReadinessStepCode, type ReadinessTaskCode } from "./types";

export interface ReadinessAction {
  to: AppRoutePath;
  label: string;
  search?: { section: SettingsSectionId };
}

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
    description: "Название и контактный email зарегистрированы в Aurum Pharma.",
  },
  {
    code: "licensed_branch",
    title: "Аптечная точка и лицензия",
    description: "Добавьте действующую аптечную точку, её адрес и срок лицензии.",
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
    description:
      "Добавьте не меньше 100 активных товаров, чтобы протестировать реальную работу кассы.",
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
): ReadinessAction | null {
  if (!definition.to || !definition.actionLabel) return null;
  if (definition.code === "pos_settings") {
    if (step.action_hint === "payment_methods_missing") {
      return {
        to: "/settings",
        label: "Настроить оплату",
        search: { section: "sales" },
      };
    }
    if (step.action_hint === "operational_branch_missing") {
      return { to: "/registers", label: "Связать кассу с точкой" };
    }
  }
  if (definition.code === "regulatory") {
    return {
      to: "/settings",
      label: definition.actionLabel,
      search: { section: "pharmacy" },
    };
  }
  return { to: definition.to, label: definition.actionLabel };
}

export interface ReadinessTaskDefinition {
  code: ReadinessTaskCode;
  title: string;
  description: string;
  action: ReadinessAction;
  requiresPos?: boolean;
}

export const readinessTasks: readonly ReadinessTaskDefinition[] = [
  {
    code: "first_incoming",
    title: "Примите первую поставку",
    description: "Проверьте добавление партий, количества и сроков годности.",
    action: { to: "/incoming", label: "Открыть приёмки" },
  },
  {
    code: "second_user",
    title: "Добавьте сотрудника",
    description: "Создайте аккаунт сотрудника и назначьте ему подходящую роль.",
    action: { to: "/users", label: "Добавить сотрудника" },
  },
  {
    code: "shift_opened",
    title: "Откройте первую смену",
    description: "Убедитесь, что рабочая касса готова к началу дня.",
    action: { to: "/pos", label: "Проверить кассу" },
    requiresPos: true,
  },
  {
    code: "first_sale",
    title: "Завершите первую продажу",
    description: "Во время настройки она тестовая и не меняет реальные остатки и выручку.",
    action: { to: "/pos", label: "Провести продажу" },
    requiresPos: true,
  },
  {
    code: "test_receipt_printed",
    title: "Проверьте печать чека",
    description: "Откройте готовый чек и запустите печать на этом устройстве.",
    action: { to: "/pos", label: "Проверить печать" },
    requiresPos: true,
  },
];

export const readinessTaskByCode = new Map(readinessTasks.map((task) => [task.code, task]));
