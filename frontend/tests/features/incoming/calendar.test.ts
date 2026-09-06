// @vitest-environment node

import { describe, expect, it } from "vitest";

import { pharmacyCalendarDate } from "@/features/incoming/calendar";

describe("pharmacyCalendarDate", () => {
  it("uses the Tajikistan calendar day across the UTC midnight boundary", () => {
    expect(pharmacyCalendarDate(new Date("2026-07-26T20:30:00Z"))).toBe("2026-07-27");
  });
});
