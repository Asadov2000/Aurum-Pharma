import { beforeEach, describe, expect, it, vi } from "vitest";

import { type MeResponse } from "@/features/auth/types";
import { clearClientSession } from "@/features/auth/session";
import { meQueryKey } from "@/features/auth/queries";
import { type SupportAccessSession } from "@/features/supportAccess/types";
import { queryClient } from "@/lib/query";
import { getAuthSessionGeneration, useAuthStore } from "@/stores/auth";
import { useSupportAccessStore } from "@/stores/supportAccess";

const mocks = vi.hoisted(() => ({
  fetchMe: vi.fn(),
  revoke: vi.fn(),
  epoch: null as string | null,
}));

vi.mock("@/features/auth/logoutState", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/features/auth/logoutState")>()),
  getAuthEpoch: () => mocks.epoch,
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

const OTHER_ACCOUNT: MeResponse = {
  ...ACCOUNT,
  id: "44444444-4444-4444-8444-444444444444",
  full_name: "Other account",
};

const OTHER_SESSION: SupportAccessSession = {
  ...SESSION,
  id: "55555555-5555-4555-8555-555555555555",
};

const OTHER_SCOPED_ACCOUNT: MeResponse = {
  ...SCOPED_ACCOUNT,
  support_access: { ...SCOPED_ACCOUNT.support_access!, id: OTHER_SESSION.id },
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((accept, fail) => {
    resolve = accept;
    reject = fail;
  });
  return { promise, resolve, reject };
}

function settle(operation: Promise<unknown>): Promise<void> {
  return operation.then(
    () => undefined,
    () => undefined,
  );
}

const NEW_CACHE_KEY = ["new-session-data"];
const NEW_CACHE_VALUE = { currentSession: true };

function cacheCurrentSession(user: MeResponse | null): void {
  queryClient.setQueryData(NEW_CACHE_KEY, NEW_CACHE_VALUE);
  if (user) queryClient.setQueryData(meQueryKey, user);
}

function expectCurrentSession(user: MeResponse | null): void {
  expect(useAuthStore.getState().user).toEqual(user);
  expect(queryClient.getQueryData(NEW_CACHE_KEY)).toEqual(NEW_CACHE_VALUE);
  expect(queryClient.getQueryData(meQueryKey)).toEqual(user ?? undefined);
}

describe("support access context", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    mocks.epoch = null;
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

  it("allows token rotation within the same session while activating support", async () => {
    useAuthStore.getState().setTokens({
      access_token: "synthetic-before-rotation",
      token_type: "bearer",
      expires_in: 900,
    });
    const generation = getAuthSessionGeneration();
    const identity = deferred<MeResponse>();
    mocks.fetchMe.mockReturnValueOnce(identity.promise);
    const activation = activateSupportContext(SESSION);
    await vi.waitFor(() => expect(mocks.fetchMe).toHaveBeenCalledOnce());

    useAuthStore.getState().setTokens({
      access_token: "synthetic-after-rotation",
      token_type: "bearer",
      expires_in: 900,
    });
    expect(getAuthSessionGeneration()).toBe(generation);
    expect(useAuthStore.getState().accessToken).toBe("synthetic-after-rotation");
    identity.resolve(SCOPED_ACCOUNT);
    await activation;

    expect(useAuthStore.getState().user).toEqual(SCOPED_ACCOUNT);
    expect(useSupportAccessStore.getState().active).toEqual(SESSION);
    expect(queryClient.getQueryData(meQueryKey)).toEqual(SCOPED_ACCOUNT);
    expect(mocks.revoke).not.toHaveBeenCalled();
  });

  it("cannot revive a support session after the same identifier was reused and cleared", async () => {
    const identity = deferred<MeResponse>();
    mocks.fetchMe.mockReturnValueOnce(identity.promise);
    const activation = settle(activateSupportContext(SESSION));
    await vi.waitFor(() => expect(mocks.fetchMe).toHaveBeenCalledOnce());
    const initialRevision = useSupportAccessStore.getState().revision;

    useSupportAccessStore.getState().clear();
    const clearedRevision = useSupportAccessStore.getState().revision;
    useSupportAccessStore.getState().setActive(SESSION);
    const reusedRevision = useSupportAccessStore.getState().revision;
    useSupportAccessStore.getState().clear();
    expect(clearedRevision).toBeGreaterThan(initialRevision);
    expect(reusedRevision).toBeGreaterThan(clearedRevision);
    expect(useSupportAccessStore.getState().revision).toBeGreaterThan(reusedRevision);
    cacheCurrentSession(ACCOUNT);
    identity.resolve(SCOPED_ACCOUNT);
    await activation;

    expectCurrentSession(ACCOUNT);
    expect(useSupportAccessStore.getState().active).toBeNull();
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

  for (const outcome of ["success", "failure"] as const) {
    for (const boundary of ["logout", "other-account", "same-account", "epoch"] as const) {
      it(`preserves the current session after late activation ${outcome} across ${boundary}`, async () => {
        const identity = deferred<MeResponse>();
        mocks.fetchMe.mockReturnValueOnce(identity.promise);
        const activation = settle(activateSupportContext(SESSION));
        await vi.waitFor(() => expect(mocks.fetchMe).toHaveBeenCalledOnce());

        let expectedUser: MeResponse | null = ACCOUNT;
        if (boundary === "epoch") {
          mocks.epoch = "another-tab-session";
        } else {
          clearClientSession();
          if (boundary === "other-account") expectedUser = OTHER_ACCOUNT;
          if (boundary === "logout") expectedUser = null;
          if (expectedUser) useAuthStore.getState().setUser(expectedUser);
        }
        cacheCurrentSession(expectedUser);

        if (outcome === "success") identity.resolve(SCOPED_ACCOUNT);
        else identity.reject(new Error("late failure"));
        await activation;

        expectCurrentSession(expectedUser);
        expect(useSupportAccessStore.getState().active).toEqual(
          boundary === "epoch" ? SESSION : null,
        );
        expect(mocks.revoke).not.toHaveBeenCalled();
      });
    }
  }

  it("does not clear a new session cache after delayed query cancellation", async () => {
    const cancellation = deferred<void>();
    vi.spyOn(queryClient, "cancelQueries").mockReturnValueOnce(cancellation.promise);
    const activation = settle(activateSupportContext(SESSION));
    expect(mocks.fetchMe).not.toHaveBeenCalled();

    clearClientSession();
    useAuthStore.getState().setUser(OTHER_ACCOUNT);
    cacheCurrentSession(OTHER_ACCOUNT);
    cancellation.resolve(undefined);
    await activation;

    expectCurrentSession(OTHER_ACCOUNT);
    expect(mocks.fetchMe).not.toHaveBeenCalled();
    expect(mocks.revoke).not.toHaveBeenCalled();
  });

  it("does not restore an old identity after revocation finishes in a new session", async () => {
    const revocation = deferred<void>();
    mocks.fetchMe.mockResolvedValueOnce(ACCOUNT);
    mocks.revoke.mockReturnValueOnce(revocation.promise);
    const activation = settle(activateSupportContext(SESSION));
    await vi.waitFor(() => expect(mocks.revoke).toHaveBeenCalledWith(SESSION.id));

    clearClientSession();
    useAuthStore.getState().setUser(OTHER_ACCOUNT);
    cacheCurrentSession(OTHER_ACCOUNT);
    revocation.resolve(undefined);
    await activation;

    expectCurrentSession(OTHER_ACCOUNT);
    expect(useSupportAccessStore.getState().active).toBeNull();
  });

  it("does not install a late cleared-context identity over a new account", async () => {
    useSupportAccessStore.getState().setActive(SESSION);
    useAuthStore.getState().setUser(SCOPED_ACCOUNT);
    const identity = deferred<MeResponse>();
    mocks.fetchMe.mockReturnValueOnce(identity.promise);
    const clearing = settle(clearSupportContext());
    await vi.waitFor(() => expect(mocks.fetchMe).toHaveBeenCalledOnce());

    clearClientSession();
    useAuthStore.getState().setUser(OTHER_ACCOUNT);
    cacheCurrentSession(OTHER_ACCOUNT);
    identity.resolve(ACCOUNT);
    await clearing;

    expectCurrentSession(OTHER_ACCOUNT);
    expect(useSupportAccessStore.getState().active).toBeNull();
  });

  it("does not clear a new session cache while support-context cancellation is pending", async () => {
    useSupportAccessStore.getState().setActive(SESSION);
    useAuthStore.getState().setUser(SCOPED_ACCOUNT);
    const cancellation = deferred<void>();
    vi.spyOn(queryClient, "cancelQueries").mockReturnValueOnce(cancellation.promise);
    const clearing = settle(clearSupportContext());

    clearClientSession();
    useAuthStore.getState().setUser(OTHER_ACCOUNT);
    cacheCurrentSession(OTHER_ACCOUNT);
    cancellation.resolve(undefined);
    await clearing;

    expectCurrentSession(OTHER_ACCOUNT);
    expect(mocks.fetchMe).not.toHaveBeenCalled();
  });

  it("preserves a new support context after an old clear operation reloads", async () => {
    useSupportAccessStore.getState().setActive(SESSION);
    useAuthStore.getState().setUser(SCOPED_ACCOUNT);
    const olderIdentity = deferred<MeResponse>();
    mocks.fetchMe.mockReturnValueOnce(olderIdentity.promise);
    const clearing = settle(clearSupportContext());
    await vi.waitFor(() => expect(mocks.fetchMe).toHaveBeenCalledOnce());

    mocks.fetchMe.mockResolvedValueOnce(OTHER_SCOPED_ACCOUNT);
    await activateSupportContext(OTHER_SESSION);
    cacheCurrentSession(OTHER_SCOPED_ACCOUNT);
    olderIdentity.resolve(ACCOUNT);
    await clearing;

    expectCurrentSession(OTHER_SCOPED_ACCOUNT);
    expect(useSupportAccessStore.getState().active).toEqual(OTHER_SESSION);
  });

  for (const outcome of ["success", "failure"] as const) {
    it(`preserves a newer support context after an older activation ${outcome}`, async () => {
      const olderIdentity = deferred<MeResponse>();
      mocks.fetchMe.mockReturnValueOnce(olderIdentity.promise);
      const olderActivation = settle(activateSupportContext(SESSION));
      await vi.waitFor(() => expect(mocks.fetchMe).toHaveBeenCalledOnce());

      mocks.fetchMe.mockResolvedValueOnce(OTHER_SCOPED_ACCOUNT);
      await activateSupportContext(OTHER_SESSION);
      cacheCurrentSession(OTHER_SCOPED_ACCOUNT);
      if (outcome === "success") olderIdentity.resolve(SCOPED_ACCOUNT);
      else olderIdentity.reject(new Error("obsolete activation"));
      await olderActivation;

      expectCurrentSession(OTHER_SCOPED_ACCOUNT);
      expect(useSupportAccessStore.getState().active).toEqual(OTHER_SESSION);
      expect(mocks.revoke).not.toHaveBeenCalled();
    });
  }
});
