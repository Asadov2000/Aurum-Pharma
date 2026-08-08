import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const searchSupplierReturnCandidates = vi.fn();
const createSupplierReturn = vi.fn();

vi.mock("@/features/suppliers/api", () => ({
  listSuppliers: vi.fn(),
  searchSuppliers: vi.fn(),
  searchSupplierOptions: vi.fn(),
  createSupplier: vi.fn(),
  updateSupplier: vi.fn(),
  searchSupplierReturns: vi.fn(),
  searchSupplierReturnCandidates: (...args: unknown[]) => searchSupplierReturnCandidates(...args),
  createSupplierReturn: (...args: unknown[]) => createSupplierReturn(...args),
}));

import { SupplierReturnForm } from "@/features/suppliers/SupplierReturnForm";

const SUPPLIER = {
  id: "supplier-1",
  tenant_id: "tenant-1",
  name: "Сино Фарм",
  legal_name: null,
  inn_or_tin: "123456789",
  contact_person: null,
  phone: null,
  email: null,
  address: null,
  notes: null,
  is_active: true,
  created_at: "2026-08-01T00:00:00Z",
  updated_at: "2026-08-01T00:00:00Z",
};

const CANDIDATE = {
  batch_id: "batch-1",
  source_document_id: "document-1",
  document_number: "ПР-1042",
  document_date: "2026-08-01",
  branch_id: "branch-1",
  branch_name: "Аптека Рудаки",
  catalog_name: "Амоксициллин",
  catalog_form: "капсулы",
  catalog_dosage: "500 мг",
  catalog_pack_size: "20 капсул",
  batch_number: "AMX-2608",
  expires_at: "2027-12-31",
  qty_remaining: "12.000",
  purchase_price: "8.50",
  currency: "TJS",
};

function renderForm(onClose = vi.fn()): void {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <SupplierReturnForm supplier={SUPPLIER} onClose={onClose} />
    </QueryClientProvider>,
  );
}

describe("SupplierReturnForm", () => {
  beforeEach(() => {
    searchSupplierReturnCandidates.mockReset();
    createSupplierReturn.mockReset();
    searchSupplierReturnCandidates.mockResolvedValue({
      items: [CANDIDATE],
      total: 1,
      page: 1,
      page_size: 20,
    });
    createSupplierReturn.mockResolvedValue({ id: "operation-1" });
    vi.stubGlobal("crypto", {
      randomUUID: () => "11111111-1111-4111-8111-111111111111",
      getRandomValues: (bytes: Uint8Array) => bytes,
    });
    Object.defineProperty(window.navigator, "onLine", { configurable: true, value: true });
  });

  it("submits a source-bound idempotent return", async () => {
    const onClose = vi.fn();
    renderForm(onClose);

    fireEvent.click(await screen.findByRole("option", { name: /Амоксициллин/i }));
    fireEvent.change(screen.getByLabelText("Количество"), { target: { value: "2,5" } });
    fireEvent.change(screen.getByLabelText("Причина"), {
      target: { value: "quality_issue" },
    });
    fireEvent.change(screen.getByLabelText("Комментарий"), {
      target: { value: "Нарушена пломба" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Оформить возврат" }));

    await waitFor(() =>
      expect(createSupplierReturn).toHaveBeenCalledWith({
        operation_id: "11111111-1111-4111-8111-111111111111",
        supplier_id: SUPPLIER.id,
        batch_id: CANDIDATE.batch_id,
        source_document_id: CANDIDATE.source_document_id,
        qty: "2.5",
        reason: "quality_issue",
        comment: "Нарушена пломба",
      }),
    );
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("blocks a quantity above the current stock", async () => {
    renderForm();
    fireEvent.click(await screen.findByRole("option", { name: /Амоксициллин/i }));
    fireEvent.change(screen.getByLabelText("Количество"), { target: { value: "13" } });
    fireEvent.change(screen.getByLabelText("Причина"), { target: { value: "damaged" } });
    fireEvent.click(screen.getByRole("button", { name: "Оформить возврат" }));

    expect(await screen.findByText("Доступно не более 12")).toBeInTheDocument();
    expect(createSupplierReturn).not.toHaveBeenCalled();
  });

  it("does not mutate inventory while the browser is offline", async () => {
    Object.defineProperty(window.navigator, "onLine", { configurable: true, value: false });
    renderForm();

    expect(
      await screen.findByText(/Возврат станет доступен после восстановления связи/i),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Оформить возврат" })).toBeDisabled();
    expect(createSupplierReturn).not.toHaveBeenCalled();
  });
});
