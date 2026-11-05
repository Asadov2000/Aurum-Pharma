import { isAxiosError } from "axios";

import { describeApiError } from "@/features/foundation/errors";

const preparationMessages: Record<string, string> = {
  "Catalog item has no unit sale preparation":
    "Сначала настройте поштучный отпуск в карточке лекарства: единицу, состав упаковки, шаг и цену.",
  "Prepared incoming quantity must be whole packs within the unit stock limit":
    "Для сохранения состава нужны целые упаковки. Уменьшите количество, если превышен предел учёта единиц.",
};

export function describeIncomingItemError(error: unknown, fallback: string): string {
  if (isAxiosError(error)) {
    const data = error.response?.data as { error?: { message?: unknown } } | undefined;
    const message = data?.error?.message;
    if (typeof message === "string" && preparationMessages[message]) {
      return preparationMessages[message];
    }
  }
  return describeApiError(error, fallback);
}
