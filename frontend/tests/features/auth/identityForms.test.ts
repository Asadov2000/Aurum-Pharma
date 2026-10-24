import { describe, expect, it } from "vitest";

import { z } from "zod";

import { emailIdentitySchema, phoneIdentitySchema, oneTimeCodeSchema, newPasswordSchema } from "@/features/auth/identityForms";

describe("new password byte boundary", () => {
  it("accepts 36 Cyrillic letters (72 UTF-8 bytes) without changing the password", () => {
    const password = "я".repeat(36);
    expect(new TextEncoder().encode(password)).toHaveLength(72);
    expect(newPasswordSchema.parse(password)).toBe(password);
  });

  it("rejects 37 Cyrillic letters (74 UTF-8 bytes) with an actionable explanation", () => {
    const result = newPasswordSchema.safeParse("я".repeat(37));
    expect(result.success).toBe(false);
    if (!result.success) expect(result.error.issues[0]?.message).toContain("36 русских букв");
  });

  it("accepts 72 ASCII characters and rejects 73", () => {
    expect(newPasswordSchema.safeParse("a".repeat(72)).success).toBe(true);
    expect(newPasswordSchema.safeParse("a".repeat(73)).success).toBe(false);
  });

  it("counts emoji as UTF-8 bytes rather than string code units", () => {
    expect(newPasswordSchema.safeParse("🔐".repeat(18)).success).toBe(true);
    expect(newPasswordSchema.safeParse("🔐".repeat(19)).success).toBe(false);
  });

  it("preserves leading and trailing whitespace and counts it toward the limit", () => {
    const password = ` ${"я".repeat(34)} `;
    expect(newPasswordSchema.parse(password)).toBe(password);
    expect(newPasswordSchema.safeParse(` ${"я".repeat(36)}`).success).toBe(false);
  });
});


describe("auth mini schemas preserve classic validation", () => {
  const baseline = {
    email: z.string().trim().min(1, "Введите email").email("Некорректный email"),
    phone: z.string().transform((value) => {
      const compact = value.replace(/[\s()-]/g, "");
      if (/^\d{9}$/.test(compact)) return `+992${compact}`;
      if (/^992\d{9}$/.test(compact)) return `+${compact}`;
      return compact;
    }).pipe(z.string().regex(/^\+992\d{9}$/, "Введите номер +992 и 9 цифр")),
    code: z.string().trim().regex(/^\d{6}$/, "Код состоит из 6 цифр"),
  };
  const mini = { email: emailIdentitySchema, phone: phoneIdentitySchema, code: oneTimeCodeSchema };
  const inputs: Record<keyof typeof mini, unknown[]> = {
    email: ["  owner@aurum.tj  ", "", "   ", "not-email", "a..b@aurum.tj", null, undefined, 123],
    phone: ["90 123 45 67", "992901234567", "+992 (90) 123-45-67", "+79012345678", "", null, undefined, 123],
    code: [" 123456 ", "12345", "1234567", "abcdef", "", null, undefined, 123456],
  };
  for (const key of ["email", "phone", "code"] as const) {
    it(`preserves ${key} parsed values and every issue`, () => {
      for (const input of inputs[key]) {
        const expected = baseline[key].safeParse(input);
        const actual = mini[key].safeParse(input);
        expect(actual.success).toBe(expected.success);
        if (actual.success && expected.success) expect(actual.data).toEqual(expected.data);
        if (!actual.success && !expected.success)
          expect(actual.error.issues).toEqual(expected.error.issues);
      }
    });
  }
});
