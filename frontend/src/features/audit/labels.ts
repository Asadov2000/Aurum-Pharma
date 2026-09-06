// Trigger actions are uppercase; older explicit events can be lowercase.
// Anything unknown falls back to the raw string.
export const actionLabel: Record<string, string> = {
  insert: "Создание",
  INSERT: "Создание",
  update: "Обновление",
  UPDATE: "Обновление",
  delete: "Удаление",
  DELETE: "Удаление",
  VIEW: "Просмотр",
  EXPORT: "Экспорт",
  IMPERSONATE: "Служебный доступ",
  MEMBERSHIP_CREATED: "Сотрудник добавлен",
  MEMBERSHIP_UPDATED: "Данные сотрудника изменены",
  MEMBERSHIP_ACTIVATED: "Доступ сотрудника активирован",
  MEMBERSHIP_SUSPENDED: "Доступ сотрудника приостановлен",
  MEMBERSHIP_OFFBOARDED: "Сотрудник отключён",
  OWNERSHIP_GRANTED: "Владелец назначен",
  OWNERSHIP_REVOKED: "Полномочия владельца отозваны",
  ROLE_PERMISSIONS_CHANGED: "Права роли изменены",
  ROLE_VERSION_PUBLISHED: "Новая версия роли опубликована",
  ROLE_ARCHIVED_WITH_REPLACEMENT: "Роль отправлена в архив",
  AUTHORIZATION_DENIED: "Опасное действие отклонено",
  login: "Вход",
  logout: "Выход",
  login_failed: "Ошибка входа",
  password_change: "Смена пароля",
  role_assign: "Назначение роли",
  role_revoke: "Отзыв роли",
  shift_open: "Открытие смены",
  shift_close: "Закрытие смены",
};

export const actionTone = (
  action: string,
): "neutral" | "success" | "warning" | "danger" | "info" => {
  const normalizedAction = action.toLowerCase();
  if (normalizedAction === "insert") return "success";
  if (normalizedAction === "update") return "info";
  if (
    normalizedAction === "delete" ||
    normalizedAction === "impersonate" ||
    normalizedAction === "authorization_denied"
  )
    return "danger";
  if (normalizedAction.includes("failed") || normalizedAction.includes("revoke")) return "danger";
  if (normalizedAction.includes("ownership") || normalizedAction.includes("permissions")) {
    return "warning";
  }
  if (normalizedAction === "export" || normalizedAction === "view") return "info";
  if (normalizedAction.startsWith("login") || normalizedAction.startsWith("logout"))
    return "neutral";
  return "neutral";
};

// Friendly table labels — pulled from the migrations.
export const tableLabel: Record<string, string> = {
  app_user: "Пользователь",
  tenant: "Тенант",
  tenant_settings: "Настройки тенанта",
  branch: "Точка",
  register: "Касса",
  role: "Роль",
  user_assignment: "Назначение роли",
  tenant_membership: "Сотрудник аптеки",
  tenant_ownership: "Владелец аптеки",
  authorization_policy: "Политика доступа",
  tenant_catalog: "Каталог",
  barcode: "Штрихкод",
  batch: "Партия",
  batch_movement: "Движение партии",
  write_off: "Списание",
  supplier: "Поставщик",
  incoming_document: "Приходный документ",
  incoming_item: "Позиция прихода",
  supplier_return: "Возврат поставщику",
  shift: "Смена",
  session: "Сеанс пользователя",
  sync_writer_epoch: "Синхронизация кассы",
  pos_command: "Команда кассы",
  sale: "Продажа",
  sale_item: "Позиция продажи",
  sale_payment: "Оплата",
  prescription_log: "Журнал рецепта",
  invoice: "Счёт",
  invoice_payment: "Платёж по счёту",
  payment: "Платёж",
  tenant_subscription: "Подписка",
  platform_access_grant_permission: "Служебный доступ",
  z_report: "Z-отчёт смены",
  sales_summary: "Сводка продаж",
  stock_on_date: "Остатки на дату",
};
