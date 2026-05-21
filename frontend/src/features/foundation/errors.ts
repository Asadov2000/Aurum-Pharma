import { AxiosError } from "axios";

export function describeApiError(err: unknown, fallback: string): string {
  if (err instanceof AxiosError) {
    const detail = (err.response?.data as { detail?: unknown } | undefined)?.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail) && detail.length > 0) {
      const first = detail[0] as { msg?: unknown };
      if (typeof first.msg === "string") return first.msg;
    }
    if (err.response?.status === 403) return "Недостаточно прав";
    if (err.response?.status === 404) return "Не найдено";
    if (err.response?.status === 429) return "Слишком много запросов";
    if (err.response?.status && err.response.status >= 500) {
      return "Сервер недоступен. Попробуйте позже.";
    }
  }
  return fallback;
}
