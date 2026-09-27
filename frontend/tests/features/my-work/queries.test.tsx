import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { type PropsWithChildren } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { type MeResponse } from "@/features/auth/types";
import { type PersonalShiftList } from "@/features/my-work/types";
import { useAuthStore } from "@/stores/auth";

vi.mock("@/features/my-work/api", () => ({ fetchPersonalShifts: vi.fn() }));
import { fetchPersonalShifts } from "@/features/my-work/api";
import { usePersonalShiftsQuery } from "@/features/my-work/queries";

const fetchShifts = vi.mocked(fetchPersonalShifts);
const previousAuth = useAuthStore.getState();
let client: QueryClient;
const emptyHistory: PersonalShiftList = {
  items: [],
  total: 0,
  page: 1,
  page_size: 10,
  timezone: "Asia/Dushanbe",
};
function employee(id = "employee-a", tenant = "tenant-a"): MeResponse {
  return {
    id,
    home_tenant_id: tenant,
    active_tenant_id: tenant,
    email: "work-fixture@example.test",
    full_name: "Work fixture",
    is_developer: false,
    is_administrator: false,
    is_tenant_owner: false,
    status: "active",
    last_login_at: null,
    level: 1,
    branch_assignments: {},
    permissions: [],
    platform_capabilities: [],
    support_access: null,
  };
}
function wrapper({ children }: PropsWithChildren) {
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

describe("own shift query scope", () => {
  beforeEach(() => {
    fetchShifts.mockReset();
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
    useAuthStore.setState({ accessToken: "synthetic-access", user: employee(), hydrated: true });
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

  it("never reuses another user, tenant or page's result while loading", async () => {
    fetchShifts.mockResolvedValueOnce(emptyHistory).mockImplementation(() => new Promise(() => {}));
    const { result, rerender } = renderHook(({ page }) => usePersonalShiftsQuery(page), {
      initialProps: { page: 1 },
      wrapper,
    });
    await waitFor(() => expect(result.current.data).toEqual(emptyHistory));
    act(() => useAuthStore.getState().setUser(employee("employee-b")));
    await waitFor(() => expect(fetchShifts).toHaveBeenCalledTimes(2));
    expect(result.current.data).toBeUndefined();
    act(() => useAuthStore.getState().setUser(employee("employee-b", "tenant-b")));
    await waitFor(() => expect(fetchShifts).toHaveBeenCalledTimes(3));
    expect(result.current.data).toBeUndefined();
    rerender({ page: 2 });
    await waitFor(() => expect(fetchShifts).toHaveBeenCalledTimes(4));
    expect(result.current.data).toBeUndefined();
    expect(fetchShifts.mock.calls[3][0]).toBe(2);
    expect(fetchShifts.mock.calls[3][1]).toBeInstanceOf(AbortSignal);
  });

  it("does not fetch without a session or while acting as platform support", async () => {
    useAuthStore.setState({ accessToken: null });
    const { rerender } = renderHook(() => usePersonalShiftsQuery(1), { wrapper });
    expect(fetchShifts).not.toHaveBeenCalled();
    act(() =>
      useAuthStore.setState({
        accessToken: "synthetic-access",
        user: { ...employee(), is_administrator: true },
      }),
    );
    rerender();
    expect(fetchShifts).not.toHaveBeenCalled();
    fetchShifts.mockResolvedValue(emptyHistory);
    act(() => useAuthStore.getState().setUser(employee()));
    await waitFor(() => expect(fetchShifts).toHaveBeenCalledOnce());
  });
});
