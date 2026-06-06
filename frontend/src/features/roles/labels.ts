// Display labels + the privilege-level helpers the role builder relies on.
// Kept framework-free so they can be unit-tested directly.

/** Role privilege tiers. Matches the backend: 1 = developer (strongest) …
 *  4 = seller/cashier (weakest). owner/seller are tenant roles now, but the
 *  numeric levels still describe the tier. */
export const LEVEL_LABEL: Record<number, string> = {
  1: "Разработчик",
  2: "Администратор",
  3: "Владелец",
  4: "Кассир",
};

export function levelLabel(level: number): string {
  return LEVEL_LABEL[level] ?? `Уровень ${level}`;
}

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

interface LevelUser {
  is_developer?: boolean;
  is_administrator?: boolean;
  permissions?: string[];
}

/** Effective privilege level of the current user — mirrors the backend
 *  CurrentUser.level so the UI refuses escalation before the API would. */
export function currentUserLevel(user: LevelUser | null | undefined): number {
  if (user?.is_developer) return 1;
  if (user?.is_administrator) return 2;
  const perms = user?.permissions ?? [];
  if (perms.includes("users.invite") || perms.includes("roles.assign")) return 3;
  return 4;
}

/** Levels a user may give a new role: strictly weaker than their own (a higher
 *  number), capped at 4. An owner (3) can therefore only mint level-4 roles. */
export function allowedRoleLevels(currentLevel: number): number[] {
  const levels: number[] = [];
  for (let l = currentLevel + 1; l <= 4; l += 1) levels.push(l);
  return levels;
}
