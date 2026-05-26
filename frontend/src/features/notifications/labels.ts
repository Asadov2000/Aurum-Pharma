import { type Channel, type Severity } from "./types";

export const severityLabel: Record<Severity, string> = {
  info: "Инфо",
  warning: "Предупреждение",
  error: "Ошибка",
  critical: "Критическое",
};

export const severityTone: Record<
  Severity,
  "neutral" | "success" | "warning" | "danger" | "info"
> = {
  info: "info",
  warning: "warning",
  error: "danger",
  critical: "danger",
};

export const severityOptions: Severity[] = ["info", "warning", "error", "critical"];

export const channelLabel: Record<Channel, string> = {
  in_app: "В системе",
  email: "Email",
  telegram: "Telegram",
  sms: "SMS",
};

// telegram + sms доставка прибудет в Этапе 2. UI рисует их disabled,
// чтобы пользователь видел план развития, но не мог включить.
export const channelAvailable: Record<Channel, boolean> = {
  in_app: true,
  email: true,
  telegram: false,
  sms: false,
};

export const allChannels: Channel[] = ["in_app", "email", "telegram", "sms"];

// Известный каталог событий — события, которые сегодня действительно
// генерируют бэкенд-задачи. Подписки на неизвестные типы остаются
// невидимыми пользователю, но всё ещё работают через бэк.
export interface EventDef {
  key: string;
  title: string;
  description: string;
}

export const knownEvents: EventDef[] = [
  {
    key: "license_expiring",
    title: "Истекает лицензия",
    description: "За 30 дней до окончания лицензии точки.",
  },
  {
    key: "trial_ending",
    title: "Заканчивается пробный период",
    description: "За 3 дня до окончания trial-периода тенанта.",
  },
  {
    key: "invoice_due",
    title: "Счёт к оплате",
    description: "Появился новый счёт; срок ещё не вышел.",
  },
  {
    key: "invoice_overdue",
    title: "Счёт просрочен",
    description: "Срок оплаты прошёл, счёт не закрыт.",
  },
  {
    key: "import_completed",
    title: "Импорт каталога завершён",
    description: "Фоновая загрузка каталога завершилась.",
  },
];
