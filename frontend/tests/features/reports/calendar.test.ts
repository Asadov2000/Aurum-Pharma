import { describe, expect, it } from "vitest";

import {
  calendarDateInTimeZone,
  currentReportMonthRange,
  recentReportRange,
} from "@/features/reports/calendar";

describe("report calendar", () => {
  const afterDushanbeMidnight = new Date("2026-07-26T20:42:00Z");

  it("uses the pharmacy timezone instead of the browser timezone", () => {
    expect(calendarDateInTimeZone(afterDushanbeMidnight, "Asia/Dushanbe")).toBe("2026-07-27");
  });

  it("builds recent and month ranges from the pharmacy calendar date", () => {
    expect(recentReportRange("Asia/Dushanbe", afterDushanbeMidnight)).toEqual({
      dateFrom: "2026-06-27",
      dateTo: "2026-07-27",
    });
    expect(currentReportMonthRange("Asia/Dushanbe", afterDushanbeMidnight)).toEqual({
      dateFrom: "2026-07-01",
      dateTo: "2026-07-27",
    });
  });
});
