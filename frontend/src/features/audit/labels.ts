// Action labels — backend writes lowercase verbs through Postgres triggers.
// Anything unknown falls back to the raw string.
export const actionLabel: Record<string, string> = {
  insert: "Создание",
  update: "Обновление",
  delete: "Удаление",
  login: "Вход",
  logout: "Выход",
  login_failed: "Ошибка входа",
  password_change: "Смена пароля",
  role_assign: "Назначение роли",
  role_revoke: "Отзыв роли",
  shift_open: "Открытие смены",
  shift_close: "Закрытие смены",
};

export const actionTone = (action: string): "neutral" | "success" | "warning" | "danger" | "info" => {
  if (action === "insert") return "success";
  if (action === "update") return "info";
  if (action === "delete") return "danger";
  if (action.includes("login_failed") || action.includes("failed")) return "danger";
  if (action.startsWith("login") || action.startsWith("logout")) return "neutral";
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
  sale: "Продажа",
  sale_item: "Позиция продажи",
  sale_payment: "Оплата",
  prescription_log: "Журнал рецепта",
  invoice: "Счёт",
  invoice_payment: "Платёж по счёту",
  tenant_subscription: "Подписка",
};
