import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { type PropsWithChildren } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { type MeResponse } from "@/features/auth/types";
import { type PersonalProfile } from "@/features/profile/types";
import { useAuthStore } from "@/stores/auth";

vi.mock("@/features/profile/api", () => ({ fetchPersonalProfile: vi.fn() }));

import { fetchPersonalProfile } from "@/features/profile/api";
import { usePersonalProfileQuery } from "@/features/profile/queries";

const fetchProfile = vi.mocked(fetchPersonalProfile);
const previousAuth = useAuthStore.getState();
let client: QueryClient;

function user(id = "user-a", tenantId = "tenant-a"): MeResponse {
  return {
    id,
    email: "profile-fixture@example.test",
    full_name: "Profile fixture",
    is_developer: false,
    is_administrator: false,
    home_tenant_id: tenantId,
    active_tenant_id: tenantId,
    status: "active",
    last_login_at: null,
    level: 1,
    is_tenant_owner: false,
    branch_assignments: {},
    permissions: [],
    platform_capabilities: [],
    support_access: null,
  };
}

function profile(id: string, tenantId: string, roleName: string): PersonalProfile {
  return {
    id,
    full_name: "Profile fixture",
    email: "profile-fixture@example.test",
    status: "active",
    tenant_id: tenantId,
    is_tenant_owner: false,
    platform_role: null,
    access_assignments: [{ role_id: roleName, role_name: roleName, scope: "tenant", branch: null }],
  };
}

function deferredProfile() {
  let resolve!: (value: PersonalProfile) => void;
  const promise = new Promise<PersonalProfile>((yes) => {
    resolve = yes;
  });
  return { promise, resolve };
}

function wrapper({ children }: PropsWithChildren) {
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

describe("personal profile query scope", () => {
  beforeEach(() => {
    fetchProfile.mockReset();
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
    useAuthStore.setState({ accessToken: "synthetic-access", user: user(), hydrated: true });
  });

  afterEach(() => {
    cleanup();
    client.clear();
    useAuthStore.setState({
      accessToken: previousAuth.accessToken,
      user: previousAuth.user,
      hydrated: previousAuth.hydrated,
    });
  });

  it("hides cached assignments when the user or tenant changes while the next fetch is pending", async () => {
    const original = profile("user-a", "tenant-a", "Original role");
    const anotherUser = profile("user-b", "tenant-a", "Another user's role");
    const anotherTenant = profile("user-b", "tenant-b", "Another tenant's role");
    const userFetch = deferredProfile();
    const tenantFetch = deferredProfile();
    fetchProfile
      .mockResolvedValueOnce(original)
      .mockReturnValueOnce(userFetch.promise)
      .mockReturnValueOnce(tenantFetch.promise);
    const { result } = renderHook(usePersonalProfileQuery, { wrapper });
    await waitFor(() => expect(result.current.data).toEqual(original));

    act(() => useAuthStore.getState().setUser(user("user-b", "tenant-a")));
    await waitFor(() => expect(fetchProfile).toHaveBeenCalledTimes(2));
    expect(result.current.isPending).toBe(true);
    expect(result.current.data).toBeUndefined();
    expect(
      client
        .getQueryCache()
        .getAll()
        .some((query) => query.state.data === original),
    ).toBe(true);
    await act(async () => userFetch.resolve(anotherUser));
    await waitFor(() => expect(result.current.data).toEqual(anotherUser));

    act(() => useAuthStore.getState().setUser(user("user-b", "tenant-b")));
    await waitFor(() => expect(fetchProfile).toHaveBeenCalledTimes(3));
    expect(result.current.isPending).toBe(true);
    expect(result.current.data).toBeUndefined();
    await act(async () => tenantFetch.resolve(anotherTenant));
    await waitFor(() => expect(result.current.data).toEqual(anotherTenant));
  });

  it("hides normal assignments when entering or changing support scope in the same tenant", async () => {
    const original = profile("user-a", "tenant-a", "Normal assignment");
    const supportProfile: PersonalProfile = {
      ...original,
      platform_role: "administrator",
      access_assignments: [],
    };
    const firstFetch = deferredProfile();
    const secondFetch = deferredProfile();
    fetchProfile
      .mockResolvedValueOnce(original)
      .mockReturnValueOnce(firstFetch.promise)
      .mockReturnValueOnce(secondFetch.promise);
    const { result } = renderHook(usePersonalProfileQuery, { wrapper });
    await waitFor(() => expect(result.current.data).toEqual(original));

    for (const [id, pending, calls] of [
      ["support-a", firstFetch, 2],
      ["support-b", secondFetch, 3],
    ] as const) {
      act(() =>
        useAuthStore.getState().setUser({
          ...user(),
          is_administrator: true,
          support_access: {
            id,
            tenant_id: "tenant-a",
            tenant_name: "Tenant fixture",
            reason: "Test support context",
            capabilities: [],
            is_read_only: true,
            expires_at: "2027-01-01T00:00:00Z",
          },
        }),
      );
      await waitFor(() => expect(fetchProfile).toHaveBeenCalledTimes(calls));
      expect(result.current.isPending).toBe(true);
      expect(result.current.data).toBeUndefined();
      expect(
        client
          .getQueryCache()
          .getAll()
          .some((query) => query.state.data === original),
      ).toBe(true);
      await act(async () => pending.resolve(supportProfile));
      await waitFor(() => expect(result.current.data).toEqual(supportProfile));
    }
  });
});
