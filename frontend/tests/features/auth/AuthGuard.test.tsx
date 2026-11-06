import { QueryClientProvider, useQuery } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, expect, it, vi } from "vitest";

import { AuthGuard } from "@/features/auth/AuthGuard";
import { type MeResponse } from "@/features/auth/types";
import { queryClient } from "@/lib/query";
import { useAuthStore } from "@/stores/auth";

const auth = vi.hoisted(() => ({ me: null as MeResponse | null, navigate: vi.fn() }));
vi.mock("@/features/auth/meQueries", () => ({
  useMeQuery: () => ({ data: auth.me, error: null, isLoading: false }),
}));
vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => auth.navigate,
  useRouterState: () => ({ pathname: "/batches" }),
}));

afterEach(() => {
  cleanup();
  queryClient.clear();
  useAuthStore.getState().clear();
  auth.navigate.mockClear();
});

it("drops both an existing query observer and page-local copies after branch access is removed", async () => {
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
  useAuthStore.getState().setUser(cashier);
  useAuthStore
    .getState()
    .setTokens({ access_token: "access", token_type: "bearer", expires_in: 900 });
  auth.me = cashier;
  queryClient.setQueryData(["inventory", "batches"], ["Точка B"]);
  const read = vi.fn(async () => ["Точка A"]);
  function ProtectedPage() {
    const { data } = useQuery({ queryKey: ["inventory", "batches"], queryFn: read });
    const [localCopy] = useState(data?.[0] ?? "Нет выбранной партии");
    return (
      <>
        <div>{data?.join(", ")}</div>
        <div>{localCopy}</div>
      </>
    );
  }
  const app = () => (
    <QueryClientProvider client={queryClient}>
      <AuthGuard>
        <ProtectedPage />
      </AuthGuard>
    </QueryClientProvider>
  );
  const view = render(app());
  expect(screen.getAllByText("Точка B")).toHaveLength(2);
  auth.me = {
    ...cashier,
    branch_assignments: { A: "role" },
    permission_scopes: { "batches.view": ["A"] },
  };
  view.rerender(app());
  await waitFor(() => expect(screen.queryByText("Точка B")).not.toBeInTheDocument());
  expect(await screen.findByText("Точка A")).toBeInTheDocument();
  expect(read).toHaveBeenCalledTimes(1);
  expect(auth.navigate).not.toHaveBeenCalled();
  expect(useAuthStore.getState().accessToken).toBe("access");
});
