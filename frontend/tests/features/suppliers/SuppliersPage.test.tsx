import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const listSuppliers = vi.fn();
const createSupplier = vi.fn();

vi.mock("@/features/suppliers/api", () => ({
  listSuppliers: (...a: unknown[]) => listSuppliers(...a),
  createSupplier: (...a: unknown[]) => createSupplier(...a),
  updateSupplier: vi.fn(),
  listSupplierReturns: vi.fn(),
  createSupplierReturn: vi.fn(),
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

describe("SuppliersPage", () => {
  beforeEach(() => {
    listSuppliers.mockReset();
    createSupplier.mockReset();
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the empty state when no suppliers exist", async () => {
    listSuppliers.mockResolvedValueOnce([]);
    renderPage();
    expect(await screen.findByText(/Поставщиков пока нет/i)).toBeInTheDocument();
  });

  it("renders rows returned from the API", async () => {
    listSuppliers.mockResolvedValueOnce([SAMPLE]);
    renderPage();
    expect(await screen.findByText("ОсОО Прима-Фарм")).toBeInTheDocument();
    expect(screen.getByText("Иван Иванов")).toBeInTheDocument();
    expect(screen.getByText("prima@example.tj")).toBeInTheDocument();
  });

  it("creates a supplier with empty optionals serialized as null", async () => {
    listSuppliers.mockResolvedValue([]);
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
});
