import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { type MeResponse } from "@/features/auth/types";
import { PhysicalLotsPanel } from "@/features/inventory/PhysicalLotsPanel";
import { api } from "@/lib/api";

const state = vi.hoisted(() => ({
  user: null as MeResponse | null,
  mode: "full",
  blocked: [] as string[],
}));
vi.mock("@/features/auth/hooks", () => ({ useAuth: () => ({ user: state.user }) }));
vi.mock("@/lib/api", () => ({ api: { get: vi.fn(), patch: vi.fn() } }));
vi.mock("@/features/tenantAccess/queries", () => ({
  useTenantAccessPolicy: () => ({
    data: {
      tenant_id: state.user?.active_tenant_id,
      mode: state.mode,
      blocked_modules: state.blocked,
    },
  }),
}));
vi.mock("@/features/pos/posStorageScope", () => ({
  capturePosSessionGuard: (scope: { userId: string; tenantId: string }) => () =>
    state.user?.id === scope.userId && state.user.active_tenant_id === scope.tenantId,
}));
vi.mock("@/features/foundation/queries", () => ({
  useBranchesQuery: () => ({
    data: [
      {
        id: "00000000-0000-4000-8000-000000000003",
        tenant_id: state.user?.active_tenant_id,
        name: "Аптека 1",
      },
    ],
  }),
}));
const id = (value: number) => `00000000-0000-4000-8000-${String(value).padStart(12, "0")}`;
const row = {
  lot_id: id(4),
  branch_id: id(3),
  catalog_id: id(5),
  catalog_name: "Таблетки",
  expires_at: "2027-10-20",
  packaging: {
    version: 2,
    unit: "tablet",
    units_per_pack: 20,
    units_per_blister: 10,
    dispense_step: 1,
  },
  closed_packs: "4",
  opened_units: "0",
  base_units: "80",
  is_blocked: false,
  blocking_revision: "0",
};
const page = (isBlocked = false) => ({
  data: { items: [{ ...row, is_blocked: isBlocked }], total: 1, page: 1, page_size: 25 },
});
let client: QueryClient;
function view() {
  return (
    <QueryClientProvider client={client}>
      <PhysicalLotsPanel />
    </QueryClientProvider>
  );
}
async function load() {
  fireEvent.change(screen.getByLabelText("Аптека"), { target: { value: id(3) } });
  fireEvent.click(screen.getByRole("button", { name: "Показать партии" }));
  await screen.findByText("Таблетки");
}
beforeEach(() => {
  vi.clearAllMocks();
  state.user = {
    id: id(1),
    active_tenant_id: id(2),
    is_developer: false,
    permissions: ["batches.view", "batches.update"],
    permission_scopes: { "batches.view": null, "batches.update": null },
    support_access: null,
  } as MeResponse;
  state.mode = "full";
  state.blocked = [];
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  vi.mocked(api.get).mockResolvedValue(page());
  vi.mocked(api.patch).mockResolvedValue({
    data: { lot_id: id(4), branch_id: id(3), is_blocked: true, blocking_revision: "1" },
  });
});
afterEach(() => {
  cleanup();
  client.clear();
});

describe("physical lot workspace", () => {
  it("requires branch, displays physical quantities and applies all filters", async () => {
    render(view());
    expect(api.get).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Показать партии" }));
    await screen.findByRole("alert");
    fireEvent.change(screen.getByLabelText("Название или ID партии"), {
      target: { value: "  Таблетки  " },
    });
    fireEvent.change(screen.getByLabelText("Состояние"), { target: { value: "blocked" } });
    fireEvent.click(screen.getByLabelText("Показывать пустые"));
    await load();
    expect(screen.getByText("80 табл.")).toBeInTheDocument();
    expect(api.get).toHaveBeenCalledWith(
      "/batches/physical",
      expect.objectContaining({
        params: expect.objectContaining({
          branch_id: id(3),
          q: "Таблетки",
          is_blocked: true,
          show_empty: true,
        }),
      }),
    );
  });
  it("confirms an explicit desired state with the reviewed revision", async () => {
    render(view());
    await load();
    fireEvent.click(screen.getByRole("button", { name: "Заблокировать" }));
    expect(api.patch).not.toHaveBeenCalled();
    vi.mocked(api.get).mockResolvedValue(page(true));
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить" }));
    await screen.findByText("Партия заблокирована.");
    expect(api.patch).toHaveBeenCalledTimes(1);
    expect(api.patch).toHaveBeenCalledWith(`/batches/physical/${id(4)}/blocking`, {
      branch_id: id(3),
      is_blocked: true,
      expected_blocking_revision: "0",
    });
    await screen.findByRole("button", { name: "Разблокировать" });
  });
  it.each(["readonly", "inventory blocked", "view only", "wrong branch scope"])(
    "disables changes for %s",
    async (kind) => {
      if (kind === "readonly") state.mode = "readonly";
      if (kind === "inventory blocked") state.blocked = ["inventory"];
      if (kind === "view only") state.user!.permissions = ["batches.view"];
      if (kind === "wrong branch scope")
        state.user!.permission_scopes!["batches.update"] = [id(10)];
      render(view());
      await load();
      expect(screen.getByRole("button", { name: "Заблокировать" })).toBeDisabled();
      expect(api.patch).not.toHaveBeenCalled();
    },
  );
  it.each(["lost response", "stale revision"])(
    "refreshes after %s without automatic inverse or retry",
    async (reason) => {
      vi.mocked(api.patch).mockRejectedValue(new Error(reason));
      render(view());
      await load();
      fireEvent.click(screen.getByRole("button", { name: "Заблокировать" }));
      vi.mocked(api.get).mockResolvedValue(page(true));
      fireEvent.click(screen.getByRole("button", { name: "Подтвердить" }));
      await screen.findByText(/Проверьте состояние партии/);
      await screen.findByRole("button", { name: "Разблокировать" });
      expect(api.patch).toHaveBeenCalledTimes(1);
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    },
  );
  it("rejects response with another branch", async () => {
    vi.mocked(api.get).mockResolvedValue({
      data: { ...page().data, items: [{ ...row, branch_id: id(10) }] },
    });
    render(view());
    fireEvent.change(screen.getByLabelText("Аптека"), { target: { value: id(3) } });
    fireEvent.click(screen.getByRole("button", { name: "Показать партии" }));
    await screen.findByRole("alert");
    expect(screen.queryByText("Таблетки")).not.toBeInTheDocument();
  });
  it.each(["tenant", "user", "permission scope"])(
    "hides cached rows and closes confirmation after switching %s",
    async (kind) => {
      const rendered = render(view());
      await load();
      fireEvent.click(screen.getByRole("button", { name: "Заблокировать" }));
      state.user =
        kind === "tenant"
          ? { ...state.user!, active_tenant_id: id(9) }
          : kind === "user"
            ? { ...state.user!, id: id(9) }
            : { ...state.user!, permission_scopes: { "batches.view": [], "batches.update": [] } };
      rendered.rerender(view());
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      expect(screen.queryByText("Таблетки")).not.toBeInTheDocument();
      expect(api.patch).not.toHaveBeenCalled();
    },
  );
  it("does not display completion from a previous session", async () => {
    let finish!: (value: { data: object }) => void;
    vi.mocked(api.patch).mockReturnValue(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    const rendered = render(view());
    await load();
    fireEvent.click(screen.getByRole("button", { name: "Заблокировать" }));
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить" }));
    await waitFor(() => expect(api.patch).toHaveBeenCalledTimes(1));
    state.user = { ...state.user!, id: id(9) };
    rendered.rerender(view());
    await act(async () => {
      finish({
        data: { lot_id: id(4), branch_id: id(3), is_blocked: true, blocking_revision: "1" },
      });
    });
    expect(screen.queryByText("Партия заблокирована.")).not.toBeInTheDocument();
  });
  it("does not accept a blocking result for a different lot", async () => {
    vi.mocked(api.patch).mockResolvedValue({
      data: { lot_id: id(90), branch_id: id(3), is_blocked: true, blocking_revision: "1" },
    });
    render(view());
    await load();
    fireEvent.click(screen.getByRole("button", { name: "Заблокировать" }));
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить" }));
    await screen.findByText(/Проверьте состояние партии/);
    expect(screen.queryByText("Партия заблокирована.")).not.toBeInTheDocument();
    expect(api.patch).toHaveBeenCalledTimes(1);
  });
});
