// Wizard steps — spec §17.1.
export interface WizardStep {
  step: number;
  title: string;
  description: string;
  linkTo?: string;
  linkLabel?: string;
}

export const wizardSteps: WizardStep[] = [
  {
    step: 1,
    title: "Профиль аптеки",
    description: "Название, контакты, юридические реквизиты.",
    linkTo: "/admin/tenants",
    linkLabel: "Заполнить в «Тенантах»",
  },
  {
    step: 2,
    title: "Первая точка",
    description: "Адрес, тип (аптека / пункт / киоск), номер и срок лицензии.",
    linkTo: "/branches",
    linkLabel: "Создать точку",
  },
  {
    step: 3,
    title: "Реквизиты для чека",
    description: "Шапка чека: название, адрес, ИНН/TIN, контактные данные.",
    linkTo: "/branches",
    linkLabel: "Открыть точку",
  },
  {
    step: 4,
    title: "Первый сотрудник",
    description: "Вы уже зарегистрированы как владелец. Дополнительные сотрудники — на странице «Пользователи».",
    linkTo: "/users",
    linkLabel: "Пригласить сотрудников",
  },
  {
    step: 5,
    title: "Загрузка каталога",
    description: "Не меньше 100 товаров. Это блокирующее условие для старта пробного периода.",
    linkTo: "/catalog",
    linkLabel: "Перейти в каталог",
  },
  {
    step: 6,
    title: "Настройки кассы",
    description: "Печать, режимы, PIN-режим для смены кассиров.",
    linkTo: "/settings",
    linkLabel: "Открыть настройки",
  },
  {
    step: 7,
    title: "Регуляторика",
    description: "Текст напоминания о лицензии и предупреждения по рецепту.",
    linkTo: "/settings",
    linkLabel: "Открыть настройки",
  },
  {
    step: 8,
    title: "Готово",
    description: "Завершите визард — на этом шаге чек-лист закрепляется как пройденный.",
  },
];

// Checklist tasks — names come from backend track_event() callers.
// We render labels we know; unknowns fall through to the raw key.
export const taskLabel: Record<string, string> = {
  catalog_loaded: "Каталог загружен (≥100 товаров)",
  first_incoming: "Создан первый приход",
  first_sale: "Первая тестовая продажа",
  second_user: "Приглашён второй сотрудник",
  shift_opened: "Открыта смена",
  test_receipt_printed: "Распечатан тестовый чек",
};
