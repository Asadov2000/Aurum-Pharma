import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { type PropsWithChildren } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { type MeResponse } from "@/features/auth/types";
import { useAuthStore } from "@/stores/auth";

vi.mock("@/features/staff-pay/api", () => ({
  listStaffPay: vi.fn(),
  searchStaffPayEmployees: vi.fn(),
}));
import { listStaffPay, searchStaffPayEmployees } from "@/features/staff-pay/api";
import { useStaffPayEmployees, useStaffPayPeriods } from "@/features/staff-pay/queries";

const previousAuth = useAuthStore.getState();
let client: QueryClient;
const empty = { items: [], total: 0, page: 1, page_size: 10, timezone: "Asia/Dushanbe" };
function employee(id = "employee-a", tenant = "tenant-a"): MeResponse {
  return {
    id,
    home_tenant_id: tenant,
    active_tenant_id: tenant,
    email: "synthetic@example.test",
    full_name: "Synthetic",
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
describe("staff pay private query scope", () => {
  beforeEach(() => {
    vi.mocked(listStaffPay).mockReset();
    vi.mocked(searchStaffPayEmployees).mockReset();
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
    useAuthStore.setState({ accessToken: "synthetic", user: employee(), hydrated: true });
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
  it("waits for reveal and isolates account, tenant, mode and page while new data loads", async () => {
    vi.mocked(listStaffPay)
      .mockResolvedValueOnce(empty)
      .mockImplementation(() => new Promise(() => {}));
    const { result, rerender } = renderHook(
      ({ own, page, visible }) => useStaffPayPeriods(own, own ? null : "target", page, visible),
      { initialProps: { own: true, page: 1, visible: false }, wrapper },
    );
    expect(listStaffPay).not.toHaveBeenCalled();
    rerender({ own: true, page: 1, visible: true });
    await waitFor(() => expect(result.current.data).toEqual(empty));
    act(() => useAuthStore.getState().setUser(employee("employee-b")));
    await waitFor(() => expect(listStaffPay).toHaveBeenCalledTimes(2));
    expect(result.current.data).toBeUndefined();
    act(() => useAuthStore.getState().setUser(employee("employee-b", "tenant-b")));
    await waitFor(() => expect(listStaffPay).toHaveBeenCalledTimes(3));
    expect(result.current.data).toBeUndefined();
    rerender({ own: true, page: 2, visible: true });
    await waitFor(() => expect(listStaffPay).toHaveBeenCalledTimes(4));
    expect(result.current.data).toBeUndefined();
    act(() =>
      useAuthStore
        .getState()
        .setUser({ ...employee("employee-b", "tenant-b"), permissions: ["staff_pay.view"] }),
    );
    rerender({ own: false, page: 1, visible: true });
    await waitFor(() => expect(listStaffPay).toHaveBeenCalledTimes(5));
    expect(result.current.data).toBeUndefined();
  });
  it("does not search staff or fetch salary as platform administrator", () => {
    useAuthStore.setState({
      user: { ...employee(), is_administrator: true, permissions: ["staff_pay.manage"] },
    });
    renderHook(
      () => {
        useStaffPayPeriods(true, null, 1);
        useStaffPayEmployees("", 1);
      },
      { wrapper },
    );
    expect(listStaffPay).not.toHaveBeenCalled();
    expect(searchStaffPayEmployees).not.toHaveBeenCalled();
  });
  it("keeps ordinary own access separate from manager search permission", () => {
    renderHook(() => useStaffPayEmployees("", 1), { wrapper });
    expect(searchStaffPayEmployees).not.toHaveBeenCalled();
  });
});
