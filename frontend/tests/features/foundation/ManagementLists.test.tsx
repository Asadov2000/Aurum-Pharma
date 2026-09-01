import { type AnchorHTMLAttributes, type ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const listBranches = vi.fn();
const searchBranches = vi.fn();
const searchRegisters = vi.fn();
const getBranchLifecycleImpact = vi.fn();
const deleteBranch = vi.fn();
const updateBranch = vi.fn();

vi.mock("@tanstack/react-router", () => ({
  Link: ({
    to,
    children,
    ...props
  }: AnchorHTMLAttributes<HTMLAnchorElement> & { to: string; children: ReactNode }) => (
    <a href={to} {...props}>
      {children}
    </a>
  ),
}));

vi.mock("@/features/foundation/api", () => ({
  listTenants: vi.fn(),
  createTenant: vi.fn(),
  updateTenant: vi.fn(),
  createTenantOwner: vi.fn(),
  createTenantMember: vi.fn(),
  getTenantSettings: vi.fn(),
  updateTenantSettings: vi.fn(),
  listBranches: (...args: unknown[]) => listBranches(...args),
  searchBranches: (...args: unknown[]) => searchBranches(...args),
  createBranch: vi.fn(),
  updateBranch: (...args: unknown[]) => updateBranch(...args),
  deleteBranch: (...args: unknown[]) => deleteBranch(...args),
  getBranchLifecycleImpact: (...args: unknown[]) => getBranchLifecycleImpact(...args),
  listRegisters: vi.fn(),
  searchRegisters: (...args: unknown[]) => searchRegisters(...args),
  createRegister: vi.fn(),
  updateRegister: vi.fn(),
  deleteRegister: vi.fn(),
}));

let mockUser: Record<string, unknown> = {};
vi.mock("@/features/auth/hooks", () => ({
  useAuth: () => ({ user: mockUser }),
}));

import { BranchesPage } from "@/features/foundation/BranchesPage";
import { RegistersPage } from "@/features/foundation/RegistersPage";

const BRANCH = {
  id: "branch-0001",
  tenant_id: "tenant-1",
  name: "Аптека Рудаки",
  address: "проспект Рудаки, 10",
  branch_type: "pharmacy" as const,
  license_number: "TJ-PH-001",
  license_expires_at: null,
  working_hours: null,
  receipt_header: null,
  is_active: true,
  created_at: "2026-07-01T00:00:00Z",
  updated_at: "2026-07-01T00:00:00Z",
};

const REGISTER = {
  id: "register-0001",
  tenant_id: "tenant-1",
  branch_id: BRANCH.id,
  name: "Касса 01",
  printer_type: "thermal_80" as const,
  printer_config: null,
  is_active: true,
  created_at: "2026-07-01T00:00:00Z",
  updated_at: "2026-07-01T00:00:00Z",
};

function renderPage(page: JSX.Element) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}>{page}</QueryClientProvider>);
}

describe("management list filters", () => {
  beforeEach(() => {
    window.localStorage.clear();
    mockUser = {
      id: "owner-1",
      home_tenant_id: "tenant-1",
      permissions: [
        "branches.view",
        "branches.create",
        "branches.update",
        "branches.delete",
        "registers.view",
        "registers.create",
        "registers.update",
        "registers.delete",
      ],
    };
    listBranches.mockReset();
    searchBranches.mockReset();
    searchRegisters.mockReset();
    getBranchLifecycleImpact.mockReset();
    deleteBranch.mockReset();
    updateBranch.mockReset();
    listBranches.mockResolvedValue([BRANCH]);
    searchBranches.mockResolvedValue({
      items: [BRANCH],
      total: 1,
      page: 1,
      page_size: 25,
    });
    searchRegisters.mockResolvedValue({
      items: [REGISTER],
      total: 1,
      page: 1,
      page_size: 25,
    });
    getBranchLifecycleImpact.mockResolvedValue({
      branch_id: BRANCH.id,
      branch_name: BRANCH.name,
      is_active: true,
      can_deactivate: true,
      active_register_count: 2,
      open_shift_count: 0,
      active_assignment_count: 4,
      active_edge_node_count: 1,
    });
    deleteBranch.mockResolvedValue({ ...BRANCH, is_active: false });
    updateBranch.mockResolvedValue(BRANCH);
  });

  afterEach(() => vi.clearAllMocks());

  it("searches and filters branches through the paginated contract", async () => {
    renderPage(<BranchesPage />);
    expect(await screen.findByText("Аптека Рудаки")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Торговые точки" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(
      within(screen.getByRole("region", { name: "Сводка торговых точек" })).getByText(
        "Лицензии требуют внимания на странице",
      ),
    ).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Поиск"), {
      target: { value: "  Рудаки  " },
    });
    fireEvent.change(screen.getByLabelText("Тип торговой точки"), {
      target: { value: "pharmacy" },
    });

    await waitFor(() =>
      expect(searchBranches).toHaveBeenCalledWith(
        expect.objectContaining({
          q: "Рудаки",
          branch_type: "pharmacy",
          is_active: true,
          page: 1,
          page_size: 25,
        }),
        expect.any(AbortSignal),
      ),
    );
  });

  it("filters registers by an allowed branch and keeps printer optional", async () => {
    renderPage(<RegistersPage />);
    expect(await screen.findByText("Касса 01")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Рабочие кассы" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(
      within(screen.getByRole("region", { name: "Сводка касс" })).getByText(
        "Формат чека выбран на странице",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText("Формат чека")).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Торговая точка"), {
      target: { value: BRANCH.id },
    });
    fireEvent.click(screen.getByRole("button", { name: /^Фильтры/ }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Формат чека" }));
    fireEvent.click(screen.getByRole("button", { name: /^Фильтры/ }));
    fireEvent.change(screen.getByLabelText("Формат чека"), {
      target: { value: "thermal_80" },
    });

    await waitFor(() =>
      expect(searchRegisters).toHaveBeenCalledWith(
        expect.objectContaining({
          branch_id: BRANCH.id,
          printer_type: "thermal_80",
          is_active: true,
          page: 1,
          page_size: 25,
        }),
        expect.any(AbortSignal),
      ),
    );
  });

  it("shows allowed branch names to a user with registers.view", async () => {
    mockUser = {
      id: "cashier-1",
      home_tenant_id: "tenant-1",
      permissions: ["registers.view"],
    };

    renderPage(<RegistersPage />);
    await screen.findByText("Касса 01");

    const branchFilter = screen.getByLabelText("Торговая точка");
    expect(branchFilter).toBeInTheDocument();
    expect(within(branchFilter).getByRole("option", { name: "Аптека Рудаки" })).toBeInTheDocument();
    expect(listBranches).toHaveBeenCalledWith(true);
    expect(screen.queryByRole("link", { name: "Торговые точки" })).not.toBeInTheDocument();
  });

  it("warns before discarding a new trading point", async () => {
    renderPage(<BranchesPage />);
    await screen.findByText("Аптека Рудаки");
    fireEvent.click(screen.getByRole("button", { name: "Добавить торговую точку" }));
    const editor = screen.getByRole("dialog", { name: "Добавить торговую точку" });
    fireEvent.change(within(editor).getByLabelText("Название"), {
      target: { value: "Аптека Сомони" },
    });
    fireEvent.click(within(editor).getByRole("button", { name: "Отмена" }));

    expect(screen.getByRole("dialog", { name: "Закрыть без сохранения?" })).toBeInTheDocument();
    expect(editor).toBeInTheDocument();
  });

  it("explains branch lifecycle impact before deactivation", async () => {
    renderPage(<BranchesPage />);
    await screen.findByText("Аптека Рудаки");

    fireEvent.click(screen.getByRole("button", { name: "Отключить" }));
    const dialog = await screen.findByRole("dialog", {
      name: "Отключить точку: Аптека Рудаки",
    });

    expect(
      await within(dialog).findByText("Точка готова к безопасному отключению"),
    ).toBeInTheDocument();
    expect(within(dialog).getByText("Назначенные сотрудники")).toBeInTheDocument();
    expect(within(dialog).getByText("4")).toBeInTheDocument();
    expect(getBranchLifecycleImpact).toHaveBeenCalledWith(BRANCH.id);

    fireEvent.click(within(dialog).getByRole("button", { name: "Отключить точку" }));
    await waitFor(() => expect(deleteBranch).toHaveBeenCalledWith(BRANCH.id));
  });

  it("warns before discarding a new register", async () => {
    renderPage(<RegistersPage />);
    await screen.findByText("Касса 01");
    fireEvent.click(screen.getByRole("button", { name: "Добавить рабочую кассу" }));
    const editor = screen.getByRole("dialog", { name: "Добавить рабочую кассу" });
    fireEvent.change(within(editor).getByLabelText("Название кассы"), {
      target: { value: "Касса 02" },
    });
    fireEvent.click(within(editor).getByRole("button", { name: "Отмена" }));

    expect(screen.getByRole("dialog", { name: "Закрыть без сохранения?" })).toBeInTheDocument();
    expect(editor).toBeInTheDocument();
  });

  it("does not present a load failure as an empty branch list", async () => {
    searchBranches.mockRejectedValueOnce(new Error("offline"));
    renderPage(<BranchesPage />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Не удалось загрузить торговые точки",
    );
    expect(screen.queryByText("Торговых точек пока нет")).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Сводка торговых точек" })).not.toBeInTheDocument();
  });

  it("blocks register creation until branch options can be loaded", async () => {
    listBranches.mockRejectedValue(new Error("offline"));
    renderPage(<RegistersPage />);
    await screen.findByText("Касса 01");
    fireEvent.click(screen.getByRole("button", { name: "Добавить рабочую кассу" }));
    const editor = screen.getByRole("dialog", { name: "Добавить рабочую кассу" });

    expect(await within(editor).findByRole("alert")).toHaveTextContent(
      "Не удалось загрузить торговые точки",
    );
    expect(within(editor).getByRole("button", { name: "Добавить кассу" })).toBeDisabled();
  });
});
