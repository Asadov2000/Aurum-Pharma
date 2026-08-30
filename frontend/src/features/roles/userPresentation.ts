import { type UserStatus } from "./types";

const PHARMACY_TIME_ZONE = "Asia/Dushanbe";
const lastLoginFormatter = new Intl.DateTimeFormat("ru-RU", {
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  timeZone: PHARMACY_TIME_ZONE,
});

export const userStatusTone: Record<
  UserStatus,
  "neutral" | "success" | "warning" | "danger" | "info"
> = {
  pending: "info",
  active: "success",
  suspended: "warning",
  offboarded: "danger",
};

export const userStatusLabel: Record<UserStatus, string> = {
  pending: "Ожидает активации",
  active: "Активен",
  suspended: "Приостановлен",
  offboarded: "Уволен",
};

export function employeeInitials(fullName: string): string {
  const parts = fullName.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "—";
  const value =
    parts.length === 1
      ? parts[0]!.slice(0, 2)
      : `${parts[0]!.charAt(0)}${parts[parts.length - 1]!.charAt(0)}`;
  return value.toLocaleUpperCase("ru-RU");
}

export function formatLastLogin(value: string | null): string {
  if (!value) return "Ещё не входил";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Нет данных";
  return lastLoginFormatter.format(date);
}

export function formatInvitationDeadline(value: string | null): string {
  if (!value) return "Срок не указан";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Срок не указан";
  return `до ${lastLoginFormatter.format(date)}`;
}

export function employeeCountLabel(count: number): string {
  const mod10 = count % 10;
  const mod100 = count % 100;
  if (mod10 === 1 && mod100 !== 11) return `${count} сотрудник`;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) {
    return `${count} сотрудника`;
  }
  return `${count} сотрудников`;
}
