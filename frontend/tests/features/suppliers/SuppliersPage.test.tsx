import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const searchSuppliers = vi.fn();
const createSupplier = vi.fn();
const searchSupplierReturns = vi.fn();
const searchSupplierReturnCandidates = vi.fn();
const createSupplierReturn = vi.fn();

vi.mock("@/features/suppliers/api", () => ({
  listSuppliers: vi.fn(),
  searchSuppliers: (...a: unknown[]) => searchSuppliers(...a),
  searchSupplierOptions: vi.fn(),
  createSupplier: (...a: unknown[]) => createSupplier(...a),
  updateSupplier: vi.fn(),
  searchSupplierReturns: (...a: unknown[]) => searchSupplierReturns(...a),
  searchSupplierReturnCandidates: (...a: unknown[]) => searchSupplierReturnCandidates(...a),
  createSupplierReturn: (...a: unknown[]) => createSupplierReturn(...a),
}));

vi.mock("@/features/auth/hooks", () => ({
  useAuth: () => ({
    user: {
      home_tenant_id: "t-1",
      is_developer: false,
      permissions: [
        "suppliers.view",
        "suppliers.create",
        "suppliers.update",
        "incoming.view",
        "incoming.return",
      ],
    },
  }),
}));

import { SuppliersPage } from "@/features/suppliers/SuppliersPage";

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <SuppliersPage />
    </QueryClientProvider>,
  );
}

const SAMPLE = {
  id: "s-1",
  tenant_id: "t-1",
  name: "ОсОО Прима-Фарм",
  legal_name: null,
  inn_or_tin: null,
  contact_person: "Иван Иванов",
  phone: "+992 900 12 34 56",
  email: "prima@example.tj",
  address: null,
  notes: null,
  is_active: true,
  created_at: "2026-05-23T00:00:00Z",
  updated_at: "2026-05-23T00:00:00Z",
};

const SUMMARY = {
  all_count: 1,
  active_count: 1,
  inactive_count: 0,
  with_contact_count: 1,
};

describe("SuppliersPage", () => {
  beforeEach(() => {
    window.localStorage.clear();
    searchSuppliers.mockReset();
    createSupplier.mockReset();
    searchSupplierReturns.mockReset();
    searchSupplierReturnCandidates.mockReset();
    createSupplierReturn.mockReset();
    searchSupplierReturns.mockResolvedValue({
      items: [],
      total: 0,
      page: 1,
      page_size: 10,
      summary: { total_qty: "0", total_amount: "0" },
    });
    searchSupplierReturnCandidates.mockResolvedValue({
      items: [],
      total: 0,
      page: 1,
      page_size: 20,
    });
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders the empty state when no suppliers exist", async () => {
    searchSuppliers.mockResolvedValueOnce({
      items: [],
      total: 0,
      page: 1,
      page_size: 25,
      summary: { ...SUMMARY, all_count: 0, active_count: 0, with_contact_count: 0 },
    });
    renderPage();
    expect(await screen.findByText(/Поставщиков пока нет/i)).toBeInTheDocument();
  });

  it("renders rows returned from the API", async () => {
    searchSuppliers.mockResolvedValueOnce({
      items: [SAMPLE],
      total: 1,
      page: 1,
      page_size: 25,
      summary: SUMMARY,
    });
    renderPage();
    expect(await screen.findByText("ОсОО Прима-Фарм")).toBeInTheDocument();
    expect(screen.getByText("Иван Иванов")).toBeInTheDocument();
    expect(screen.getByText("prima@example.tj")).toBeInTheDocument();
  });

  it("creates a supplier with empty optionals serialized as null", async () => {
    searchSuppliers.mockResolvedValue({
      items: [],
      total: 0,
      page: 1,
      page_size: 25,
      summary: { ...SUMMARY, all_count: 0, active_count: 0, with_contact_count: 0 },
    });
    createSupplier.mockResolvedValueOnce(SAMPLE);
    renderPage();
    await screen.findByText(/Поставщиков пока нет/i);
    fireEvent.click(screen.getByRole("button", { name: /Новый поставщик/i }));
    fireEvent.change(await screen.findByLabelText("Название"), {
      target: { value: "ОсОО Прима-Фарм" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^Создать$/i }));
    await waitFor(() => {
      expect(createSupplier).toHaveBeenCalledTimes(1);
    });
    // Empty optional fields land as null, not "" or undefined — matches
    // the FastAPI schema where Optional[str] = None is expected.
    expect(createSupplier).toHaveBeenCalledWith(
      expect.objectContaining({
        name: "ОсОО Прима-Фарм",
        legal_name: null,
        inn_or_tin: null,
        contact_person: null,
        phone: null,
        email: null,
        address: null,
        notes: null,
      }),
    );
  });

  it("debounces search and applies an explicit inactive status", async () => {
    searchSuppliers.mockResolvedValue({
      items: [SAMPLE],
      total: 1,
      page: 1,
      page_size: 25,
      summary: SUMMARY,
    });
    renderPage();
    await screen.findByText("ОсОО Прима-Фарм");

    fireEvent.change(screen.getByLabelText("Поиск"), {
      target: { value: "  Прима  " },
    });
    fireEvent.change(screen.getByLabelText("Статус"), {
      target: { value: "inactive" },
    });

    await waitFor(() =>
      expect(searchSuppliers).toHaveBeenCalledWith(
        expect.objectContaining({
          q: "Прима",
          is_active: false,
          page: 1,
          page_size: 25,
        }),
        expect.any(AbortSignal),
      ),
    );
  });
});
