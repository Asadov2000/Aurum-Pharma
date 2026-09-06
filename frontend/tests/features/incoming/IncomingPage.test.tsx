import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const listIncoming = vi.fn();
const getIncoming = vi.fn();
const listBranches = vi.fn();
const searchSupplierOptions = vi.fn();

vi.mock("@/features/incoming/api", () => ({
  listIncoming: (...a: unknown[]) => listIncoming(...a),
  createIncoming: vi.fn(),
  getIncoming: (...a: unknown[]) => getIncoming(...a),
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
      permissions: [
        "incoming.view",
        "incoming.create",
        "incoming.finalize",
        "branches.view",
        "suppliers.view",
      ],
      permission_scopes: {
        "incoming.view": ["b-1"],
        "incoming.create": ["b-1"],
        "incoming.finalize": ["b-1"],
        "branches.view": ["b-1"],
        "suppliers.view": null,
      },
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

const SUMMARY = {
  all_count: 1,
  draft_count: 1,
  accepted_count: 0,
  rejected_count: 0,
  accepted_amount: "0.00",
  currency: "TJS",
};

function stubViewport(split = false): void {
  vi.stubGlobal(
    "matchMedia",
    vi.fn((query: string) => ({
      matches: query.includes("768px") || (split && query.includes("1280px")),
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  );
}

describe("IncomingPage", () => {
  beforeEach(() => {
    listIncoming.mockReset();
    getIncoming.mockReset();
    listBranches.mockReset();
    searchSupplierOptions.mockReset();
    searchSupplierOptions.mockResolvedValue({
      items: [{ id: SUPPLIER.id, name: SUPPLIER.name, is_active: true }],
    });
    window.localStorage.clear();
    stubViewport();
  });
  afterEach(() => {
    vi.clearAllMocks();
    vi.unstubAllGlobals();
  });

  it("renders an empty-state hint when there are no documents", async () => {
    listIncoming.mockResolvedValueOnce({
      items: [],
      total: 0,
      page: 1,
      page_size: 25,
      summary: { ...SUMMARY, all_count: 0, draft_count: 0 },
    });
    listBranches.mockResolvedValueOnce([BRANCH]);
    renderPage();
    expect(await screen.findByText(/Приходов пока нет/i)).toBeInTheDocument();
  });

  it("shows a retryable error without a misleading empty state", async () => {
    listIncoming.mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce({
      items: [],
      total: 0,
      page: 1,
      page_size: 25,
      summary: { ...SUMMARY, all_count: 0, draft_count: 0 },
    });
    listBranches.mockResolvedValue([BRANCH]);
    renderPage();

    expect(await screen.findByRole("alert")).toHaveTextContent("Не удалось загрузить приходы");
    expect(screen.queryByText(/Приходов пока нет/i)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Повторить" }));
    expect(await screen.findByText(/Приходов пока нет/i)).toBeInTheDocument();
    expect(listIncoming).toHaveBeenCalledTimes(2);
  });

  it("resolves branch and supplier ids to names from the lookups", async () => {
    listIncoming.mockResolvedValueOnce({
      items: [DOC],
      total: 1,
      page: 1,
      page_size: 25,
      summary: SUMMARY,
    });
    listBranches.mockResolvedValueOnce([BRANCH]);
    renderPage();
    // Branch and supplier names appear both in filter dropdowns (as <option>)
    // and in the table row — scope to the table to avoid duplicate matches.
    const row = (await screen.findByText("INV-001")).closest("tr") as HTMLElement;
    expect(within(row).getByText("Аптека центр")).toBeInTheDocument();
    expect(within(row).getByText("Прима-Фарм")).toBeInTheDocument();
    expect(within(row).getByText(/Черновик/)).toBeInTheDocument();
    expect(within(row).getByText(/1\s500,00 TJS/)).toBeInTheDocument();
  });

  it("requests only the selected page and resets pagination after filtering", async () => {
    listIncoming.mockResolvedValue({
      items: [DOC],
      total: 26,
      page: 1,
      page_size: 25,
      summary: { ...SUMMARY, all_count: 26 },
    });
    listBranches.mockResolvedValue([BRANCH]);
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: /Вперёд/ }));
    await waitFor(() =>
      expect(listIncoming).toHaveBeenLastCalledWith(
        expect.objectContaining({ page: 2, page_size: 25 }),
        expect.any(AbortSignal),
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
        expect.any(AbortSignal),
      ),
    );
  });

  it("loads details only for the selected document in the wide workspace", async () => {
    stubViewport(true);
    const second = { ...DOC, id: "d-2", document_number: "INV-002" };
    listIncoming.mockResolvedValueOnce({
      items: [DOC, second],
      total: 2,
      page: 1,
      page_size: 25,
      summary: { ...SUMMARY, all_count: 2, draft_count: 2 },
    });
    getIncoming.mockImplementation(async (id: string) => ({
      ...(id === second.id ? second : DOC),
      branch_name: BRANCH.name,
      supplier_name: SUPPLIER.name,
      items: [],
    }));
    listBranches.mockResolvedValueOnce([BRANCH]);
    renderPage();

    expect(
      await screen.findByRole("region", { name: "Карточка прихода: INV-001" }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Открыть приход: INV-002" }));
    expect(
      await screen.findByRole("region", { name: "Карточка прихода: INV-002" }),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(getIncoming).toHaveBeenLastCalledWith(second.id, expect.any(AbortSignal)),
    );
  });

  it("warns before closing a new receiving document with unsaved data", async () => {
    listIncoming.mockResolvedValueOnce({
      items: [],
      total: 0,
      page: 1,
      page_size: 25,
      summary: { ...SUMMARY, all_count: 0, draft_count: 0 },
    });
    listBranches.mockResolvedValue([BRANCH]);
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "Новая приёмка" }));
    const formDialog = screen.getByRole("dialog", { name: "Новая приёмка" });
    fireEvent.change(within(formDialog).getByLabelText("Номер документа поставщика"), {
      target: { value: "ПР-TEST" },
    });
    fireEvent.click(within(formDialog).getByRole("button", { name: "Закрыть" }));

    expect(screen.getByRole("dialog", { name: "Закрыть без сохранения?" })).toBeInTheDocument();
    expect(formDialog).toBeInTheDocument();
  });
});
