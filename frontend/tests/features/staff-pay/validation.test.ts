import { describe, expect, it } from "vitest";
import { payAmount, payDateSchema } from "@/features/staff-pay/validation";

describe("manual amount entry", () => {
  it("accepts comma decimals and zero without rounding", () => {
    expect(payAmount.parse(" 123,45 ")).toBe("123.45");
    expect(payAmount.parse("0")).toBe("0");
    expect(payAmount.parse("000123.40")).toBe("123.40");
  });
  it.each(["-10", "1.234", "1e3", "NaN", "1000000000000.00", ""])(
    "rejects invalid or oversized amount %s",
    (value) => {
      expect(payAmount.safeParse(value).success).toBe(false);
    },
  );
  it("validates calendar dates instead of letting JS normalize them", () => {
    expect(payDateSchema.safeParse("2026-02-30").success).toBe(false);
    expect(payDateSchema.safeParse("2024-02-29").success).toBe(true);
  });
});
