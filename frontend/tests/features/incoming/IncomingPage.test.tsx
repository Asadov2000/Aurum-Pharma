import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const listIncoming = vi.fn();
const listBranches = vi.fn();
const searchSupplierOptions = vi.fn();

vi.mock("@/features/incoming/api", () => ({
  listIncoming: (...a: unknown[]) => listIncoming(...a),
  createIncoming: vi.fn(),
  getIncoming: vi.fn(),
  updateIncoming: vi.fn(),
  addIncomingItem: vi.fn(),
  updateIncomingItem: vi.fn(),
  deleteIncomingItem: vi.fn(),
  acceptIncoming: vi.fn(),
  rejectIncoming: vi.fn(),
}));

vi.mock("@/features/foundation/api", () => ({
  listBranches: (...a: unknown[]) => listBranches(...a),
  listTenants: vi.fn(),
  createTenant: vi.fn(),
  updateTenant: vi.fn(),
  getTenantSettings: vi.fn(),
  updateTenantSettings: vi.fn(),
  createBranch: vi.fn(),
  updateBranch: vi.fn(),
  deleteBranch: vi.fn(),
  listRegisters: vi.fn(),
  createRegister: vi.fn(),
  updateRegister: vi.fn(),
  deleteRegister: vi.fn(),
}));

vi.mock("@/features/suppliers/api", () => ({
  listSuppliers: vi.fn(),
  searchSuppliers: vi.fn(),
  searchSupplierOptions: (...a: unknown[]) => searchSupplierOptions(...a),
  createSupplier: vi.fn(),
  updateSupplier: vi.fn(),
  searchSupplierReturns: vi.fn(),
  searchSupplierReturnCandidates: vi.fn(),
  createSupplierReturn: vi.fn(),
}));

vi.mock("@/features/auth/hooks", () => ({
  useAuth: () => ({
    user: {
      home_tenant_id: "t-1",
      is_developer: false,
      permissions: ["incoming.view", "incoming.create", "branches.view", "suppliers.view"],
    },
  }),
}));

vi.mock("@tanstack/react-router", () => ({
  Link: ({ children }: { children: React.ReactNode }) => <a>{children}</a>,
  useNavigate: () => vi.fn(),
  useParams: () => ({}),
  useSearch: () => ({}),
}));

import { IncomingPage } from "@/features/incoming/IncomingPage";

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <IncomingPage />
    </QueryClientProvider>,
  );
}

const BRANCH = {
  id: "b-1",
  tenant_id: "t-1",
  name: "Аптека центр",
  address: null,
  branch_type: "pharmacy" as const,
  license_number: null,
  license_expires_at: null,
  working_hours: null,
  receipt_header: null,
  is_active: true,
  created_at: "2026-05-23T00:00:00Z",
  updated_at: "2026-05-23T00:00:00Z",
};

const SUPPLIER = {
  id: "s-1",
  tenant_id: "t-1",
  name: "Прима-Фарм",
  legal_name: null,
  inn_or_tin: null,
  contact_person: null,
  phone: null,
  email: null,
  address: null,
  notes: null,
  is_active: true,
  created_at: "2026-05-23T00:00:00Z",
  updated_at: "2026-05-23T00:00:00Z",
};

const DOC = {
  id: "d-1",
  tenant_id: "t-1",
  branch_id: BRANCH.id,
  supplier_id: SUPPLIER.id,
  supplier_name: SUPPLIER.name,
  document_number: "INV-001",
  document_date: "2026-05-22",
  status: "draft" as const,
  total_amount: "1500.00",
  currency: "TJS",
  notes: null,
  document_file_path: null,
  created_at: "2026-05-23T00:00:00Z",
  updated_at: "2026-05-23T00:00:00Z",
  accepted_at: null,
};

describe("IncomingPage", () => {
  beforeEach(() => {
    listIncoming.mockReset();
    listBranches.mockReset();
    searchSupplierOptions.mockReset();
    searchSupplierOptions.mockResolvedValue({
      items: [{ id: SUPPLIER.id, name: SUPPLIER.name, is_active: true }],
    });
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders an empty-state hint when there are no documents", async () => {
    listIncoming.mockResolvedValueOnce({ items: [], total: 0, page: 1, page_size: 25 });
    listBranches.mockResolvedValueOnce([BRANCH]);
    renderPage();
    expect(await screen.findByText(/Приходов пока нет/i)).toBeInTheDocument();
  });

  it("shows a retryable error without a misleading empty state", async () => {
    listIncoming
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValueOnce({ items: [], total: 0, page: 1, page_size: 25 });
    listBranches.mockResolvedValue([BRANCH]);
    renderPage();

    expect(await screen.findByRole("alert")).toHaveTextContent("Не удалось загрузить приходы");
    expect(screen.queryByText(/Приходов пока нет/i)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Повторить" }));
    expect(await screen.findByText(/Приходов пока нет/i)).toBeInTheDocument();
    expect(listIncoming).toHaveBeenCalledTimes(2);
  });

  it("resolves branch and supplier ids to names from the lookups", async () => {
    listIncoming.mockResolvedValueOnce({ items: [DOC], total: 1, page: 1, page_size: 25 });
    listBranches.mockResolvedValueOnce([BRANCH]);
    renderPage();
    // Branch and supplier names appear both in filter dropdowns (as <option>)
    // and in the table row — scope to the table to avoid duplicate matches.
    const row = (await screen.findByText("INV-001")).closest("tr") as HTMLElement;
    expect(within(row).getByText("Аптека центр")).toBeInTheDocument();
    expect(within(row).getByText("Прима-Фарм")).toBeInTheDocument();
    expect(within(row).getByText(/Черновик/)).toBeInTheDocument();
    expect(within(row).getByText(/1500\.00 TJS/)).toBeInTheDocument();
  });

  it("requests only the selected page and resets pagination after filtering", async () => {
    listIncoming.mockResolvedValue({ items: [DOC], total: 26, page: 1, page_size: 25 });
    listBranches.mockResolvedValue([BRANCH]);
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: /Вперёд/ }));
    await waitFor(() =>
      expect(listIncoming).toHaveBeenLastCalledWith(
        expect.objectContaining({ page: 2, page_size: 25 }),
      ),
    );

    fireEvent.change(screen.getByLabelText("Номер документа"), {
      target: { value: "INV-001" },
    });
    await waitFor(() =>
      expect(listIncoming).toHaveBeenLastCalledWith(
        expect.objectContaining({
          document_number: "INV-001",
          page: 1,
          page_size: 25,
        }),
      ),
    );
  });
});
