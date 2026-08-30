// Central mapping of backend errors → friendly Russian text.
//
// The backend wraps domain errors in a uniform envelope
//   { "error": { "code": ..., "message": ..., "details": {...} } }
// (see backend/app/middleware/error_handler.py). FastAPI's own validation
// errors and HTTPExceptions use the legacy `{ "detail": ... }` shape, so we
// handle both. Domain `code`s are coarse (business_rule_violation, not_found,
// …), so for precise wording we key off the stable English `message` string;
// the `code` map is the category fallback.

import { AxiosError } from "axios";

/** Exact backend `message` → Russian text (+ suggested action where useful). */
const MESSAGE_MAP: Record<string, string> = {
  // --- POS / sales ---
  "Insufficient stock for this catalog item":
    "Недостаточно товара на складе. Уменьшите количество или примите новый приход.",
  "Insufficient stock at checkout":
    "Товара не хватило на момент оплаты. Обновите чек и попробуйте снова.",
  "Insufficient payment": "Оплата меньше суммы чека. Добавьте платёж на полную сумму.",
  "Prescription log required before completing a Rx sale":
    "Нужны данные рецепта. Заполните рецепт перед завершением продажи.",
  "Cannot complete a sale with no items": "В чеке нет позиций — добавьте товар.",
  "Sale is voided": "Эта продажа отменена.",
  "Only forward sales can be refunded": "Возврат возможен только для обычной продажи.",
  "Refund quantity exceeds what's left on this line":
    "Количество возврата больше, чем осталось по позиции.",
  "Batch disappeared mid-checkout": "Партия стала недоступна. Повторите добавление.",
  "Catalog item not found": "Позиция каталога не найдена.",
  "Sale item not found": "Позиция в чеке не найдена.",
  "Sale item not found in this sale": "Позиция в этом чеке не найдена.",
  "Sale not found": "Продажа не найдена.",
  "Shift is not open": "Смена не открыта. Откройте смену, чтобы продолжить.",
  "No open shift for this register": "Для этой кассы нет открытой смены.",
  "Register already has an open shift":
    "На этой кассе уже открыта смена другого сотрудника. Выберите другую кассу или попросите управляющего закрыть смену.",
  "Shift not found": "Смена не найдена.",
  "Register not found": "Касса не найдена.",
  "Register is inactive": "Касса неактивна.",

  // --- inventory ---
  "Cannot write off a blocked batch": "Партия заблокирована — списание невозможно.",
  "Batch not found": "Партия не найдена.",

  // --- catalog / import ---
  "Import job not found": "Задача импорта не найдена.",
  "Import has already been rolled back": "Импорт уже откатан.",
  "Job has no uploaded file": "Файл не загружен. Загрузите CSV и повторите.",
  "Barcode not found": "Штрихкод не найден.",
  "No catalog item for this barcode": "По этому штрихкоду нет позиции каталога.",

  // --- incoming ---
  "Incoming document not found": "Приёмка не найдена.",
  "Cannot accept a document with no items": "Сначала добавьте товар.",
  "Item not found": "Позиция не найдена.",

  // --- billing ---
  "Invoice not found": "Счёт не найден.",
  "Invoice is not payable": "Счёт нельзя оплатить — он уже оплачен или отменён.",
  "Subscription not found": "Подписка не найдена.",
  "Subscription plan not found": "Тарифный план не найден.",
  "Subscription is already cancelled": "Подписка уже отменена.",
  "No default plan configured": "Не настроен тариф по умолчанию. Обратитесь в поддержку.",

  // --- foundation / roles ---
  "Cannot deactivate the last active branch of the tenant":
    "Нельзя деактивировать последнюю активную точку.",
  "Branch does not belong to this tenant": "Точка не принадлежит этой аптеке.",
  "Branch is inactive": "Точка неактивна.",
  "Branch not found": "Точка не найдена.",
  "Tenant not found": "Аптека не найдена.",
  "Settings not found for tenant": "Настройки аптеки не найдены.",
  "Role not found": "Роль не найдена.",
  "User not found": "Пользователь не найден.",
  "User not found in this tenant": "Пользователь не найден в этой аптеке.",
  "User does not exist": "Пользователь не существует.",
  "User already has an active assignment for this branch":
    "У пользователя уже есть роль на этой точке.",
  "Password must be configured before it can be required at login":
    "Сотрудник ещё не настроил пароль. Пока оставьте вход по коду.",
  "Support privileges required": "Требуются права поддержки.",
  "Invalid authentication code": "Неверный или устаревший код подтверждения.",
  "Invalid or replayed authentication code":
    "Этот код уже использован или устарел. Введите новый код.",
  "Invalid recovery code": "Неверный или уже использованный резервный код.",
  "Invalid or expired MFA challenge": "Время подтверждения истекло. Начните вход заново.",
  "Support MFA is unavailable": "Двухфакторная защита недоступна. Войдите в систему заново.",
  "Request is not scoped to a tenant":
    "Действие требует выбранной аптеки. Войдите под учётной записью аптеки.",

  // --- onboarding ---
  "Wizard is already completed": "Мастер настройки уже завершён.",
  "Wizard not initialised for this tenant": "Мастер настройки ещё не создан.",
};

/** Domain `code` → category fallback when the message is unknown. */
const CODE_MAP: Record<string, string> = {
  not_found: "Запрашиваемые данные не найдены.",
  validation_error: "Проверьте правильность введённых данных.",
  business_rule_violation: "Действие невозможно по правилам системы.",
  conflict: "Конфликт данных — обновите страницу и попробуйте снова.",
  permission_denied: "Недостаточно прав для этого действия.",
  authentication_required: "Требуется вход в систему.",
  rate_limited: "Слишком много запросов. Подождите немного.",
  internal_error: "Внутренняя ошибка сервера. Попробуйте позже.",
};

/** Status-code fallback for responses without a recognizable envelope. */
const STATUS_MAP: Record<number, string> = {
  401: "Требуется вход в систему.",
  403: "Недостаточно прав для этого действия.",
  404: "Не найдено.",
  409: "Конфликт данных — обновите страницу и попробуйте снова.",
  422: "Проверьте правильность введённых данных.",
  429: "Слишком много запросов. Подождите немного.",
};

const GENERIC_FALLBACK = "Произошла ошибка. Попробуйте ещё раз.";

// Heuristic: does a string look like Russian (Cyrillic present)? Used to
// decide whether a raw server `detail` is safe to show to the user.
function looksRussian(s: string): boolean {
  return /[А-Яа-яЁё]/.test(s);
}

interface Envelope {
  error?: { code?: unknown; message?: unknown };
  detail?: unknown;
}

/**
 * Turn any thrown value into a user-facing Russian message.
 * @param err     the caught error (usually an AxiosError)
 * @param fallback message to use when nothing more specific is known
 */
export function describeApiError(err: unknown, fallback = GENERIC_FALLBACK): string {
  if (!(err instanceof AxiosError)) {
    return fallback;
  }

  // 1. Network / timeout — no HTTP response came back at all.
  if (!err.response) {
    if (err.code === "ECONNABORTED" || err.message.toLowerCase().includes("timeout")) {
      return "Сервер не ответил вовремя. Попробуйте ещё раз.";
    }
    return "Не удалось связаться с сервером. Проверьте соединение.";
  }

  const data = err.response.data as Envelope | undefined;

  // 2. Domain envelope: { error: { code, message } }.
  const envelope = data?.error;
  if (envelope) {
    const message = typeof envelope.message === "string" ? envelope.message : "";
    // Most specific first: an exact known English message.
    if (message && MESSAGE_MAP[message]) return MESSAGE_MAP[message];
    // Then a message the backend already wrote in Russian — trust it over
    // the coarse code category.
    if (message && looksRussian(message)) return message;
    // Finally the code category as a generic fallback.
    const code = typeof envelope.code === "string" ? envelope.code : "";
    if (code && CODE_MAP[code]) return CODE_MAP[code];
  }

  // 3. FastAPI shapes: detail as string (HTTPException) or array (validation).
  const detail = data?.detail;
  if (typeof detail === "string" && detail) {
    return looksRussian(detail) ? detail : (STATUS_MAP[err.response.status] ?? fallback);
  }
  if (Array.isArray(detail) && detail.length > 0) {
    const first = detail[0] as { msg?: unknown };
    if (typeof first.msg === "string" && looksRussian(first.msg)) return first.msg;
    return STATUS_MAP[422] ?? fallback;
  }

  // 4. Bare status-code fallback.
  const byStatus = STATUS_MAP[err.response.status];
  if (byStatus) return byStatus;
  if (err.response.status >= 500) return "Сервер недоступен. Попробуйте позже.";

  return fallback;
}
