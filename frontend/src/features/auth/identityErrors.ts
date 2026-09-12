import { isAxiosError } from "axios";
import { describeAccountSecurityError } from "./accountSecurityErrors";

export function describeIdentityError(error: unknown, fallback: string): string {
  if (isAxiosError(error) && error.response?.status === 401) {
    return "Не удалось подтвердить вход. Проверьте данные и код; если забыли пароль, восстановите его.";
  }
  return describeAccountSecurityError(error, fallback);
}
