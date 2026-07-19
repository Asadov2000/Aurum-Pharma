import { describe, expect, it } from "vitest";

import { hasAnyPermission, hasPermission } from "@/features/auth/permissions";

describe("permission helpers", () => {
  it("accepts an explicit permission and rejects an absent one", () => {
    const user = { is_developer: false, permissions: ["catalog.view"] };

    expect(hasPermission(user, "catalog.view")).toBe(true);
    expect(hasPermission(user, "catalog.update")).toBe(false);
    expect(hasAnyPermission(user, ["catalog.update", "catalog.view"])).toBe(true);
  });

  it("keeps the developer bypass and handles partial auth snapshots defensively", () => {
    expect(hasPermission({ is_developer: true, permissions: [] }, "anything")).toBe(true);
    expect(hasPermission(null, "anything")).toBe(false);
    expect(hasPermission({ is_developer: false }, "anything")).toBe(false);
  });
});
