import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const getCurrentShift = vi.fn();
const getSale = vi.fn();
const addPayment = vi.fn();

vi.mock("@/features/pos/api", () => ({
  getCurrentShift: (...a: unknown[]) => getCurrentShift(...a),
  getSale: (...a: unknown[]) => getSale(...a),
  addPayment: (...a: unknown[]) => addPayment(...a),
  openShift: vi.fn(),
  closeShift: vi.fn(),
  getZReport: vi.fn(),
  getZReportXlsx: vi.fn(),
  createSale: vi.fn(),
  addSaleItem: vi.fn(),
  updateSaleItem: vi.fn(),
  deleteSaleItem: vi.fn(),
  completeSale: vi.fn(),
  addPrescription: vi.fn(),
  getReceipt: vi.fn(),
  getReceiptPdf: vi.fn(),
}));

vi.mock("@/features/catalog/queries", () => ({
  useCatalogQuery: () => ({ data: undefined }),
}));

vi.mock("@/features/catalog/api", () => ({
  findByBarcode: vi.fn(),
}));

import { SaleArea } from "@/features/pos/SaleArea";
import { draftKey } from "@/features/pos/draftStorage";

const REG = "reg-1";

const SHIFT = {
  id: "sh-1",
  tenant_id: "t-1",
  branch_id: "b-1",
  register_id: REG,
  opened_by_user_id: "u-1",
  closed_by_user_id: null,
  opened_at: "2026-05-23T08:00:00Z",
  closed_at: null,
  status: "open" as const,
  opening_cash: "100.00",
  closing_cash_actual: null,
  closing_cash_expected: null,
  closing_difference: null,
  totals: null,
  currency: "TJS",
  notes: null,
};

const SALE = {
  id: "sale-1",
  tenant_id: "t-1",
  branch_id: "b-1",
  register_id: REG,
  shift_id: "sh-1",
  sale_type: "sale" as const,
  parent_sale_id: null,
  status: "draft" as const,
  receipt_number: null,
  is_test: false,
  total_amount: "50.00",
  currency: "TJS",
  voided_at: null,
  voided_by_sale_id: null,
  cashier_user_id: "u-1",
  created_at: "2026-05-23T08:05:00Z",
  completed_at: null,
  items: [
    {
      id: "it-1",
      sale_id: "sale-1",
      catalog_id: "c-1",
      batch_id: "ba-1",
      qty: "1",
      unit_price: "50.00",
      total_price: "50.00",
      currency: "TJS",
      discount_amount: "0",
      position: 1,
    },
  ],
  payments: [],
};

function renderArea() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <SaleArea registerId={REG} mode="keyboard" soundOn={false} draftTtlMin={30} />
    </QueryClientProvider>,
  );
}

describe("SaleArea — Enter pays cash", () => {
  beforeEach(() => {
    window.localStorage.clear();
    getCurrentShift.mockReset();
    getSale.mockReset();
    addPayment.mockReset();
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("Enter on a ready draft records a cash payment for the remaining balance", async () => {
    // Seed a live draft so the workspace restores an existing sale on mount.
    window.localStorage.setItem(
      draftKey(REG),
      JSON.stringify({ saleId: SALE.id, nameById: {}, savedAt: Date.now() }),
    );
    getCurrentShift.mockResolvedValue(SHIFT);
    getSale.mockResolvedValue(SALE);
    addPayment.mockResolvedValue({ id: "pay-1" });

    renderArea();

    // Wait until the sale is loaded (Оплачено/Остаток only render once totalDue > 0).
    await screen.findByText(/Остаток/);

    fireEvent.keyDown(window, { key: "Enter" });

    await waitFor(() => expect(addPayment).toHaveBeenCalled());
    expect(addPayment).toHaveBeenCalledWith("sale-1", {
      payment_method: "cash",
      amount: "50.00",
    });
  });
});
