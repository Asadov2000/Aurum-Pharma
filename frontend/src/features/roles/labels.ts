/** Russian section titles for the permission groups (permission.group_code). */
export const GROUP_LABEL: Record<string, string> = {
  users: "Сотрудники",
  roles: "Роли",
  branches: "Точки",
  registers: "Кассы",
  catalog: "Каталог",
  batches: "Партии",
  suppliers: "Поставщики",
  incoming: "Приходы",
  pos: "Касса",
  sales: "Чеки",
  reports: "Отчёты",
  audit: "Аудит",
  settings: "Настройки",
  tenant: "Аптека",
};

export function groupLabel(code: string): string {
  return GROUP_LABEL[code] ?? code;
}
