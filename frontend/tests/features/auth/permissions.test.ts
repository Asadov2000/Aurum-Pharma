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

  it("uses the scoped permission snapshot during developer support access", () => {
    const scopedDeveloper = {
      is_developer: true,
      permissions: ["catalog.view"],
      support_access: {
        id: "support-session",
        tenant_id: "tenant-1",
        tenant_name: "Аптека",
        reason: "Поддержка",
        capabilities: ["catalog.view"],
        is_read_only: true,
        expires_at: "2026-08-01T00:00:00Z",
      },
    };

    expect(hasPermission(scopedDeveloper, "catalog.view")).toBe(true);
    expect(hasPermission(scopedDeveloper, "catalog.update")).toBe(false);
  });
});
