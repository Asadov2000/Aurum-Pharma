import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const getCurrentShift = vi.fn();
const getSale = vi.fn();
const addPayment = vi.fn();
const completeSale = vi.fn();
const requestDesktopCashDrawerOpen = vi.fn();

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
  completeSale: (...a: unknown[]) => completeSale(...a),
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

vi.mock("@/lib/desktopBridge", () => ({
  DESKTOP_BARCODE_SCANNED_EVENT: "aurum-desktop-barcode-scanned",
  normalizeDesktopBarcode: (rawCode: string) => rawCode.trim() || null,
  requestDesktopCashDrawerOpen: (...a: unknown[]) =>
    requestDesktopCashDrawerOpen(...a),
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

const CASH_PAYMENT = {
  id: "pay-cash-1",
  sale_id: SALE.id,
  payment_method: "cash" as const,
  amount: "50.00",
  currency: "TJS",
};

const CARD_PAYMENT = {
  ...CASH_PAYMENT,
  id: "pay-card-1",
  payment_method: "card" as const,
};

function renderArea() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <SaleArea registerId={REG} mode="keyboard" soundOn={false} draftTtlMin={30} />
    </QueryClientProvider>,
  );
}

describe("SaleArea", () => {
  beforeEach(() => {
    window.localStorage.clear();
    getCurrentShift.mockReset();
    getSale.mockReset();
    addPayment.mockReset();
    completeSale.mockReset();
    requestDesktopCashDrawerOpen.mockReset();
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("Enter on a ready draft records a cash payment for the remaining balance", async () => {
    // Seed a live draft so the workspace restores an existing sale on mount.
    seedDraftSale(SALE.id);
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

  it("requests the desktop cash drawer after completing a cash sale", async () => {
    const sale = { ...SALE, payments: [CASH_PAYMENT] };
    seedDraftSale(sale.id);
    getCurrentShift.mockResolvedValue(SHIFT);
    getSale.mockResolvedValue(sale);
    completeSale.mockResolvedValue({
      ...sale,
      completed_at: "2026-05-23T08:10:00Z",
      receipt_number: "R-1",
      status: "completed",
    });

    renderArea();

    await screen.findByText(/Остаток/);
    fireEvent.click(await screen.findByRole("button", { name: /Завершить продажу/i }));

    await waitFor(() => expect(completeSale).toHaveBeenCalledWith(SALE.id));
    expect(requestDesktopCashDrawerOpen).toHaveBeenCalledWith({
      reason: "sale-completed",
      registerId: REG,
      saleId: SALE.id,
    });
  });

  it("does not request the desktop cash drawer after a non-cash sale", async () => {
    const sale = { ...SALE, payments: [CARD_PAYMENT] };
    seedDraftSale(sale.id);
    getCurrentShift.mockResolvedValue(SHIFT);
    getSale.mockResolvedValue(sale);
    completeSale.mockResolvedValue({
      ...sale,
      completed_at: "2026-05-23T08:10:00Z",
      receipt_number: "R-2",
      status: "completed",
    });

    renderArea();

    await screen.findByText(/Остаток/);
    fireEvent.click(await screen.findByRole("button", { name: /Завершить продажу/i }));

    await waitFor(() => expect(completeSale).toHaveBeenCalledWith(SALE.id));
    expect(requestDesktopCashDrawerOpen).not.toHaveBeenCalled();
  });

  it("does not request the desktop cash drawer when completing the sale fails", async () => {
    const sale = { ...SALE, payments: [CASH_PAYMENT] };
    seedDraftSale(sale.id);
    getCurrentShift.mockResolvedValue(SHIFT);
    getSale.mockResolvedValue(sale);
    completeSale.mockRejectedValue(new Error("backend failed"));

    renderArea();

    await screen.findByText(/Остаток/);
    fireEvent.click(await screen.findByRole("button", { name: /Завершить продажу/i }));

    await waitFor(() => expect(completeSale).toHaveBeenCalledWith(SALE.id));
    await waitFor(() =>
      expect(screen.getByText(/Не удалось завершить продажу/i)).toBeInTheDocument(),
    );
    expect(requestDesktopCashDrawerOpen).not.toHaveBeenCalled();
  });

  it("does not complete or open the desktop cash drawer twice while completion is pending", async () => {
    const sale = { ...SALE, payments: [CASH_PAYMENT] };
    let resolveComplete: (value: unknown) => void = () => undefined;
    seedDraftSale(sale.id);
    getCurrentShift.mockResolvedValue(SHIFT);
    getSale.mockResolvedValue(sale);
    completeSale.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveComplete = resolve;
        }),
    );

    renderArea();

    await screen.findByText(/Остаток/);
    fireEvent.keyDown(window, { key: "F4" });
    fireEvent.keyDown(window, { key: "F4" });

    await waitFor(() => expect(completeSale).toHaveBeenCalledTimes(1));
    expect(requestDesktopCashDrawerOpen).not.toHaveBeenCalled();

    resolveComplete({
      ...sale,
      completed_at: "2026-05-23T08:10:00Z",
      receipt_number: "R-3",
      status: "completed",
    });

    await waitFor(() => expect(requestDesktopCashDrawerOpen).toHaveBeenCalledTimes(1));
  });
});

function seedDraftSale(saleId: string): void {
  window.localStorage.setItem(
    draftKey(REG),
    JSON.stringify({ saleId, nameById: {}, savedAt: Date.now() }),
  );
}
