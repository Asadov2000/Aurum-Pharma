import { beforeEach, describe, expect, it, vi } from "vitest";

const post = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    post: (...args: unknown[]) => post(...args),
  },
}));

import { createTenantMember } from "@/features/foundation/api";

describe("foundation admin API", () => {
  beforeEach(() => {
    post.mockReset();
  });

  it("creates a tenant membership without asking for a tenant UUID in the payload", async () => {
    const membership = {
      membership_id: "membership-1",
      user_id: "user-1",
      tenant_id: "tenant-1",
      email: "member@aurum.tj",
      full_name: "Новый Сотрудник",
      phone: null,
      status: "pending",
    };
    post.mockResolvedValue({ data: membership });

    await expect(
      createTenantMember("tenant-1", {
        email: "member@aurum.tj",
        full_name: "Новый Сотрудник",
        phone: null,
      }),
    ).resolves.toEqual(membership);
    expect(post).toHaveBeenCalledWith("/admin/tenants/tenant-1/members", {
      email: "member@aurum.tj",
      full_name: "Новый Сотрудник",
      phone: null,
    });
  });
});
