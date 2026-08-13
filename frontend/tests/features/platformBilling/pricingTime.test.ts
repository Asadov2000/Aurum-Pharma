import { describe, expect, it } from "vitest";

import {
  formatPricingDateTime,
  minimumPricingLocalInput,
  parsePricingLocalInput,
} from "@/features/platformBilling/pricingTime";

describe("platform pricing time", () => {
  it("interprets publishing time in Asia/Dushanbe independently of the browser timezone", () => {
    expect(parsePricingLocalInput("2026-10-01T09:30")?.toISOString()).toBe(
      "2026-10-01T04:30:00.000Z",
    );
    expect(formatPricingDateTime("2026-10-01T04:30:00.000Z")).toContain("09:30");
  });

  it("builds the minimum input from the configured notice period", () => {
    expect(minimumPricingLocalInput(30, Date.parse("2026-08-01T05:00:00.000Z"))).toBe(
      "2026-08-31T10:01",
    );
  });

  it("rejects normalized or malformed local dates", () => {
    expect(parsePricingLocalInput("2026-02-31T09:00")).toBeNull();
    expect(parsePricingLocalInput("2026-10-01 09:00")).toBeNull();
  });
});
