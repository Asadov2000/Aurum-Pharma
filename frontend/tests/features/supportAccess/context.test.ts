import { beforeEach, describe, expect, it, vi } from "vitest";

import { type MeResponse } from "@/features/auth/types";
import { type SupportAccessSession } from "@/features/supportAccess/types";
import { queryClient } from "@/lib/query";
import { useAuthStore } from "@/stores/auth";
import { useSupportAccessStore } from "@/stores/supportAccess";

const mocks = vi.hoisted(() => ({
  fetchMe: vi.fn(),
  revoke: vi.fn(),
}));

vi.mock("@/features/auth/api", () => ({
  fetchMe: () => mocks.fetchMe(),
}));

vi.mock("@/features/supportAccess/api", () => ({
  revokeSupportSession: (sessionId: string) => mocks.revoke(sessionId),
}));

import { activateSupportContext, clearSupportContext } from "@/features/supportAccess/context";

const SESSION: SupportAccessSession = {
  id: "11111111-1111-4111-8111-111111111111",
  tenant_id: "22222222-2222-4222-8222-222222222222",
  tenant_name: "Шифо",
  actor_user_id: "33333333-3333-4333-8333-333333333333",
  reason: "Настройка ролей перед запуском",
  capabilities: ["users.view"],
  is_read_only: true,
  started_at: "2026-07-22T10:00:00Z",
  expires_at: "2026-07-22T10:15:00Z",
  revoked_at: null,
};

const ACCOUNT: MeResponse = {
  id: SESSION.actor_user_id,
  email: "admin@aurum.tj",
  full_name: "Support Admin",
  is_developer: false,
  is_administrator: true,
  home_tenant_id: null,
  active_tenant_id: null,
  status: "active",
  last_login_at: null,
  level: 3,
  is_tenant_owner: false,
  branch_assignments: {},
  permissions: [],
  platform_capabilities: [],
  support_access: null,
};

const SCOPED_ACCOUNT: MeResponse = {
  ...ACCOUNT,
  active_tenant_id: SESSION.tenant_id,
  permissions: SESSION.capabilities,
  support_access: {
    id: SESSION.id,
    tenant_id: SESSION.tenant_id,
    tenant_name: SESSION.tenant_name,
    reason: SESSION.reason,
    capabilities: SESSION.capabilities,
    is_read_only: SESSION.is_read_only,
    expires_at: SESSION.expires_at,
  },
};

describe("support access context", () => {
  beforeEach(() => {
    queryClient.clear();
    useSupportAccessStore.getState().clear();
    useAuthStore.getState().setUser(ACCOUNT);
    mocks.fetchMe.mockReset();
    mocks.revoke.mockReset().mockResolvedValue(undefined);
  });

  it("commits the tenant identity only after /me confirms the exact session", async () => {
    mocks.fetchMe.mockResolvedValueOnce(SCOPED_ACCOUNT);

    await activateSupportContext(SESSION);

    expect(useSupportAccessStore.getState().active).toEqual(SESSION);
    expect(useAuthStore.getState().user).toEqual(SCOPED_ACCOUNT);
    expect(mocks.revoke).not.toHaveBeenCalled();
  });

  it("revokes and restores the previous identity when /me does not confirm it", async () => {
    mocks.fetchMe.mockResolvedValueOnce(ACCOUNT);

    await expect(activateSupportContext(SESSION)).rejects.toThrow(
      "Support access identity mismatch",
    );

    expect(mocks.revoke).toHaveBeenCalledWith(SESSION.id);
    expect(useSupportAccessStore.getState().active).toBeNull();
    expect(useAuthStore.getState().user).toEqual(ACCOUNT);
  });

  it("drops elevated local state immediately even when identity refresh is offline", async () => {
    useSupportAccessStore.getState().setActive(SESSION);
    useAuthStore.getState().setUser(SCOPED_ACCOUNT);
    mocks.fetchMe.mockRejectedValueOnce(new Error("offline"));

    const refresh = clearSupportContext();

    expect(useSupportAccessStore.getState().active).toBeNull();
    expect(useAuthStore.getState().user).toEqual(ACCOUNT);
    await expect(refresh).rejects.toThrow("offline");
    expect(useAuthStore.getState().user).toEqual(ACCOUNT);
  });
});
