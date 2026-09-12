import { describe, expect, it } from "vitest";

import { newPasswordSchema } from "@/features/auth/identityForms";

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
