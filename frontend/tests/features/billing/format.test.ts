import { describe, expect, it } from "vitest";

import { formatBillingDate } from "@/features/billing/format";

describe("billing date formatting", () => {
  it("uses the Tajikistan calendar day regardless of the computer time zone", () => {
    expect(formatBillingDate("2026-05-21T20:30:00Z")).toBe("22.05.2026");
  });
});
