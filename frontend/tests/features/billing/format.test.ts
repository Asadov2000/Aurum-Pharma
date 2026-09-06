// @vitest-environment node

import { describe, expect, it } from "vitest";

import { billingYear, formatBillingDate, formatBillingMoney } from "@/features/billing/format";

describe("billing date formatting", () => {
  it("uses the Tajikistan calendar day regardless of the computer time zone", () => {
    expect(formatBillingDate("2026-05-21T20:30:00Z")).toBe("22.05.2026");
    expect(billingYear("2025-12-31T20:30:00Z")).toBe("2026");
  });

  it("formats TJS amounts for Russian-language users", () => {
    expect(formatBillingMoney("1650", "TJS")).toMatch(/^1[\s\u00a0]650,00 TJS$/);
  });
});
