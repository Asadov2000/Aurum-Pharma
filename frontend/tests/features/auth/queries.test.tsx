import { focusManager, onlineManager, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen } from "@testing-library/react";
import { AxiosError } from "axios";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { fetchMe } from "@/features/auth/api";
import { AuthGuard } from "@/features/auth/AuthGuard";
import { type MeResponse } from "@/features/auth/types";
import { queryClient } from "@/lib/query";
import { getAuthSessionGeneration, useAuthStore } from "@/stores/auth";

vi.mock("@/features/auth/api", () => ({ fetchMe: vi.fn() }));
vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => vi.fn(),
  useRouterState: () => ({ pathname: "/batches" }),
}));

const cashier: MeResponse = {
  id: "cashier",
  email: "synthetic@example.test",
  full_name: "Кассир",
  is_developer: false,
  is_administrator: false,
  home_tenant_id: "tenant",
  active_tenant_id: "tenant",
  status: "active",
  last_login_at: null,
  level: 4,
  is_tenant_owner: false,
  branch_assignments: { A: "role", B: "role" },
  permissions: ["batches.view"],
  permission_scopes: { "batches.view": ["A", "B"] },
  platform_capabilities: [],
  support_access: null,
};

beforeEach(() => {
  vi.useFakeTimers();
  vi.mocked(fetchMe).mockReset().mockResolvedValue(cashier);
  focusManager.setFocused(true);
  onlineManager.setOnline(true);
  useAuthStore.getState().setUser(cashier);
  useAuthStore
    .getState()
    .setTokens({ access_token: "access", token_type: "bearer", expires_in: 900 });
  queryClient.setQueryDefaults(["auth", "me"], { retry: false });
  queryClient.setQueryData(["auth", "me"], cashier);
  queryClient.setQueryData(["inventory"], [{ branch_id: "B" }]);
});

afterEach(() => {
  cleanup();
  queryClient.clear();
  queryClient.setQueryDefaults(["auth", "me"], {});
  useAuthStore.getState().clear();
  onlineManager.setOnline(true);
  focusManager.setFocused(undefined);
  vi.useRealTimers();
});

function renderGuard() {
  return render(
    <QueryClientProvider client={queryClient}>
      <AuthGuard>
        <div>Рабочая область</div>
      </AuthGuard>
    </QueryClientProvider>,
  );
}

it("applies revoked branch access from the next active-tab identity refresh", async () => {
  renderGuard();
  const generation = getAuthSessionGeneration();
  vi.mocked(fetchMe).mockResolvedValue({
    ...cashier,
    branch_assignments: { A: "role" },
    permission_scopes: { "batches.view": ["A"] },
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(60_001);
  });
  expect(fetchMe).toHaveBeenCalledTimes(1);
  expect(getAuthSessionGeneration()).toBeGreaterThan(generation);
  expect(queryClient.getQueryData(["inventory"])).toBeUndefined();
  expect(useAuthStore.getState().user?.permission_scopes).toEqual({ "batches.view": ["A"] });
  expect(screen.getByText("Рабочая область")).toBeInTheDocument();
});

it("preserves login and cached work if identity refresh temporarily fails", async () => {
  renderGuard();
  const generation = getAuthSessionGeneration();
  vi.mocked(fetchMe).mockRejectedValue(new AxiosError("Network Error", "ERR_NETWORK"));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(60_001);
  });
  expect(fetchMe).toHaveBeenCalledTimes(1);
  expect(getAuthSessionGeneration()).toBe(generation);
  expect(queryClient.getQueryData(["inventory"])).toEqual([{ branch_id: "B" }]);
  expect(useAuthStore.getState().accessToken).toBe("access");
  expect(screen.getByText("Рабочая область")).toBeInTheDocument();
});

it("does not poll while the cloud tab is offline or in the background", async () => {
  renderGuard();
  onlineManager.setOnline(false);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(60_001);
  });
  expect(fetchMe).not.toHaveBeenCalled();
  focusManager.setFocused(false);
  onlineManager.setOnline(true);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(60_001);
  });
  // Reconnection can resume one paused request; background intervals cannot add more.
  const calls = vi.mocked(fetchMe).mock.calls.length;
  await act(async () => {
    await vi.advanceTimersByTimeAsync(120_000);
  });
  expect(fetchMe).toHaveBeenCalledTimes(calls);
  expect(useAuthStore.getState().accessToken).toBe("access");
});
