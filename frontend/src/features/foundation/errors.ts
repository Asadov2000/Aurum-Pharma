import { AxiosError } from "axios";

import { describeApiError } from "@/lib/errorMessages";

export { describeApiError };

const DEACTIVATION_MESSAGES: Record<string, string> = {
  "Cannot deactivate a branch with an open shift":
    "Нельзя деактивировать торговую точку, пока на одной из её касс открыта смена.",
  "Cannot deactivate a register with an open shift":
    "Нельзя деактивировать рабочую кассу с открытой сменой. Сначала закройте смену.",
};

export function describeFoundationError(err: unknown, fallback: string): string {
  if (err instanceof AxiosError) {
    const data = err.response?.data as { error?: { message?: unknown } } | undefined;
    const message = data?.error?.message;
    if (typeof message === "string" && DEACTIVATION_MESSAGES[message]) {
      return DEACTIVATION_MESSAGES[message];
    }
  }
  return describeApiError(err, fallback);
}
