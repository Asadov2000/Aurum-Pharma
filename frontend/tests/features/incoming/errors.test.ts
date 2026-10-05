import { AxiosError, type AxiosResponse } from "axios";
import { describe, expect, it } from "vitest";

import { describeIncomingItemError } from "@/features/incoming/errors";

describe("incoming preparation errors", () => {
  it.each([
    ["Catalog item has no unit sale preparation", /Сначала настройте поштучный отпуск/],
    [
      "Prepared incoming quantity must be whole packs within the unit stock limit",
      /целые упаковки/,
    ],
  ])("explains %s in Russian", (message, expected) => {
    const error = new AxiosError("Request failed", "ERR_BAD_RESPONSE");
    error.response = {
      status: 422,
      data: { error: { code: "business_rule_violation", message } },
      statusText: "",
      headers: {},
      config: {},
    } as AxiosResponse;
    expect(describeIncomingItemError(error, "Не удалось сохранить позицию")).toMatch(expected);
  });
  it("preserves the ordinary fallback for non-API errors", () => {
    expect(describeIncomingItemError(new Error("failure"), "Не удалось сохранить позицию")).toBe(
      "Не удалось сохранить позицию",
    );
  });
});
