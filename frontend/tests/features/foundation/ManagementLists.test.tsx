import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const listBranches = vi.fn();
const searchBranches = vi.fn();
const searchRegisters = vi.fn();

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
  updateBranch: vi.fn(),
  deleteBranch: vi.fn(),
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
  });

  afterEach(() => vi.clearAllMocks());

  it("searches and filters branches through the paginated contract", async () => {
    renderPage(<BranchesPage />);
    expect(await screen.findByText("Аптека Рудаки")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Поиск"), {
      target: { value: "  Рудаки  " },
    });
    fireEvent.change(screen.getByLabelText("Тип точки"), {
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
    expect(screen.queryByLabelText("Тип принтера")).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Точка"), {
      target: { value: BRANCH.id },
    });
    fireEvent.click(screen.getByRole("button", { name: /^Фильтры/ }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Тип принтера" }));
    fireEvent.click(screen.getByRole("button", { name: /^Фильтры/ }));
    fireEvent.change(screen.getByLabelText("Тип принтера"), {
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

  it("does not show or load the branch filter without branches.view", async () => {
    mockUser = {
      id: "cashier-1",
      home_tenant_id: "tenant-1",
      permissions: ["registers.view"],
    };

    renderPage(<RegistersPage />);
    await screen.findByText("Касса 01");
    fireEvent.click(screen.getByRole("button", { name: /^Фильтры/ }));

    expect(screen.queryByRole("checkbox", { name: "Точка" })).not.toBeInTheDocument();
    expect(listBranches).not.toHaveBeenCalled();
  });
});
