export const settingsCategories = [
  {
    id: "account",
    group: "personal",
    title: "Мой аккаунт",
    description: "Профиль и уровень доступа",
    keywords: "профиль пользователь учётная запись email доступ",
    source: "app_user",
  },
  {
    id: "interface",
    group: "personal",
    title: "Интерфейс",
    description: "Тема, размер и контраст",
    keywords: "цвет оформление сенсор анимация плотность",
    source: "user_preferences",
  },
  {
    id: "menu",
    group: "personal",
    title: "Меню и старт",
    description: "Порядок и избранные разделы",
    keywords: "боковая панель скрыть порядок стартовая страница",
    source: "user_preferences",
  },
  {
    id: "notifications",
    group: "personal",
    title: "Уведомления",
    description: "События и каналы",
    keywords: "оповещения события каналы",
    source: "notification_subscriptions",
  },
  {
    id: "security",
    group: "personal",
    title: "Безопасность аккаунта",
    description: "Активные сеансы и защита входа",
    keywords: "вход устройства выйти защита сессии mfa",
    source: "auth_sessions",
  },
  {
    id: "device",
    group: "device",
    title: "Касса и оборудование",
    description: "Сканер, печать и режим управления",
    keywords: "сканер чек принтер клавиатура сенсор звук рабочее место",
    source: "device_preferences",
  },
  {
    id: "pharmacy",
    group: "owner",
    title: "Рабочие правила",
    description: "Сессии, черновики и рецепты",
    keywords: "владелец сессия черновик рецепт касса правила",
    source: "tenant_settings",
  },
  {
    id: "sales",
    group: "owner",
    title: "Оплата и возвраты",
    description: "Способы оплаты и правила возврата",
    keywords: "наличные карта qr смешанная оплата возврат",
    source: "tenant_settings",
  },
  {
    id: "inventory",
    group: "owner",
    title: "Склад и сроки",
    description: "Пороги срока годности",
    keywords: "партии остатки просрочено срок годности",
    source: "tenant_settings",
  },
  {
    id: "reports",
    group: "owner",
    title: "Отчёты и рабочий день",
    description: "Часовой пояс и валюта",
    keywords: "tjs сомони душанбе дата время отчёты рабочий день",
    source: "tenant_settings",
  },
] as const;

export type SettingsCategory = (typeof settingsCategories)[number];
export type SettingsSectionId = SettingsCategory["id"];
export type SettingsCategoryGroup = SettingsCategory["group"];

export const settingsCategoryGroups: readonly SettingsCategoryGroup[] = [
  "personal",
  "device",
  "owner",
];

export const settingsGroupLabels: Record<SettingsCategoryGroup, string> = {
  personal: "Только для вас",
  device: "На этом устройстве",
  owner: "Для всей аптеки",
};

export const SETTINGS_SECTION_IDS: readonly SettingsSectionId[] = settingsCategories.map(
  (category) => category.id,
);

export function parseSettingsSearch(raw: Record<string, unknown>): {
  section?: SettingsSectionId;
} {
  const section = raw.section;
  return typeof section === "string" && SETTINGS_SECTION_IDS.includes(section as SettingsSectionId)
    ? { section: section as SettingsSectionId }
    : {};
}
