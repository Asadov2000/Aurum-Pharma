import { isAxiosError } from "axios";
import { describeApiError } from "@/lib/errorMessages";

const MESSAGES: Record<string, string> = {
  "Set an account password first": "Сначала создайте пароль в настройках безопасности.",
  "Invalid password": "Неверный пароль аккаунта. Попробуйте ещё раз.",
  "Too many password attempts": "Слишком много попыток ввода пароля. Повторите через 15 минут.",
  "Password confirmation guard is unavailable":
    "Подтверждение пароля временно недоступно. Попробуйте позже.",
  "Account password is already configured": "Пароль уже создан. Обновите настройки безопасности.",
  "Password setup is no longer valid": "Время настройки пароля истекло. Запросите новый код.",
  "Invalid or expired code":
    "Неверный или просроченный код. Запросите новый, если срок его действия истёк.",
  "MFA setup is unavailable or already enabled":
    "Защита уже включена или настройка недоступна. Обновите страницу.",
  "MFA setup session is inactive": "Сеанс настройки завершён. Начните настройку защиты заново.",
  "MFA confirmation is required": "Введите код из приложения или резервный код.",
  "MFA confirmation is no longer valid":
    "Подтверждение устарело. Обновите страницу и попробуйте снова.",
  "Account MFA is unavailable":
    "Двухфакторная защита недоступна. Обновите страницу и проверьте настройки.",
  "Authenticated session changed": "Сеанс изменился. Повторите подтверждение.",
};

export function describeAccountSecurityError(error: unknown, fallback: string): string {
  if (isAxiosError<{ error?: { message?: string } }>(error)) {
    const message = error.response?.data.error?.message;
    if (message && MESSAGES[message]) return MESSAGES[message];
  }
  return describeApiError(error, fallback);
}
