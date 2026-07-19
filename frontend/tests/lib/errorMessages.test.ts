import { AxiosError, type AxiosResponse } from "axios";
import { describe, expect, it } from "vitest";

import { describeApiError } from "@/lib/errorMessages";

function axiosErrorWith(status: number, data: unknown): AxiosError {
  const response = { status, data, statusText: "", headers: {}, config: {} } as AxiosResponse;
  const err = new AxiosError("Request failed", "ERR_BAD_RESPONSE");
  err.response = response;
  return err;
}

function networkError(code: string): AxiosError {
  // No response → network/timeout branch.
  return new AxiosError("Network Error", code);
}

describe("describeApiError", () => {
  it("maps a known domain message to friendly Russian + action", () => {
    const err = axiosErrorWith(422, {
      error: { code: "business_rule_violation", message: "Insufficient stock for this catalog item" },
    });
    expect(describeApiError(err)).toMatch(/Недостаточно товара на складе/);
  });

  it("maps the prescription rule to its Russian text", () => {
    const err = axiosErrorWith(422, {
      error: {
        code: "business_rule_violation",
        message: "Prescription log required before completing a Rx sale",
      },
    });
    expect(describeApiError(err)).toMatch(/данные рецепта/i);
  });

  it("explains when another employee already has the register shift", () => {
    const err = axiosErrorWith(409, {
      error: { code: "conflict", message: "Register already has an open shift" },
    });
    expect(describeApiError(err)).toMatch(/смена другого сотрудника/i);
  });

  it("falls back to the code category when the message is unknown", () => {
    const err = axiosErrorWith(409, {
      error: { code: "conflict", message: "Some brand-new untranslated message" },
    });
    expect(describeApiError(err)).toMatch(/Конфликт данных/);
  });

  it("trusts an unknown message that is already Russian", () => {
    const err = axiosErrorWith(422, {
      error: { code: "business_rule_violation", message: "Какое-то новое правило" },
    });
    expect(describeApiError(err)).toBe("Какое-то новое правило");
  });

  it("handles FastAPI string detail in Russian", () => {
    const err = axiosErrorWith(400, { detail: "Неверный формат файла" });
    expect(describeApiError(err)).toBe("Неверный формат файла");
  });

  it("handles FastAPI validation array (Russian msg)", () => {
    const err = axiosErrorWith(422, { detail: [{ msg: "Поле обязательно" }] });
    expect(describeApiError(err)).toBe("Поле обязательно");
  });

  it("uses a status fallback for an English detail", () => {
    const err = axiosErrorWith(403, { detail: "Forbidden" });
    expect(describeApiError(err)).toMatch(/Недостаточно прав/);
  });

  it("returns a network message when there is no response", () => {
    expect(describeApiError(networkError("ERR_NETWORK"))).toMatch(/связаться с сервером/i);
  });

  it("returns a timeout message for ECONNABORTED", () => {
    expect(describeApiError(networkError("ECONNABORTED"))).toMatch(/не ответил вовремя/i);
  });

  it("returns the provided fallback for a non-Axios value", () => {
    expect(describeApiError(new Error("boom"), "Запасной текст")).toBe("Запасной текст");
  });

  it("maps rate_limited code", () => {
    const err = axiosErrorWith(429, { error: { code: "rate_limited", message: "Too many requests" } });
    expect(describeApiError(err)).toMatch(/Слишком много запросов/);
  });
});
