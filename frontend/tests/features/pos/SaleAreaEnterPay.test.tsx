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
  requestDesktopCashDrawerOpen: (...a: unknown[]) => requestDesktopCashDrawerOpen(...a),
}));

import { SaleArea } from "@/features/pos/SaleArea";
import { markPendingCompletion } from "@/features/pos/completionOperation";
import { draftKey } from "@/features/pos/draftStorage";
import { createPendingPaymentOperation } from "@/features/pos/paymentOperation";

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
  operation_id: null,
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
    addPayment.mockResolvedValue({ ...CASH_PAYMENT, id: "pay-1" });

    renderArea();

    // Wait until the sale is loaded (Оплачено/Остаток only render once totalDue > 0).
    await screen.findByText(/Остаток/);

    fireEvent.keyDown(window, { key: "Enter" });

    await waitFor(() => expect(addPayment).toHaveBeenCalled());
    const paymentPayload = addPayment.mock.calls[0]?.[1] as {
      operation_id: string;
      payment_method: string;
      amount: string;
    };
    expect(paymentPayload).toEqual({
      operation_id: expect.stringMatching(
        /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i,
      ),
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
    expect(JSON.parse(window.localStorage.getItem(draftKey(REG)) ?? "{}")).toMatchObject({
      saleId: SALE.id,
      status: "completed",
    });

    fireEvent.click(await screen.findByRole("button", { name: /Новая продажа/i }));
    await waitFor(() => expect(screen.queryByText(/Чек № R-1 оформлен/i)).not.toBeInTheDocument());
  });

  it("keeps the completed receipt active when its local pointer cannot be cleared", async () => {
    const sale = { ...SALE, payments: [CASH_PAYMENT] };
    seedDraftSale(sale.id);
    getCurrentShift.mockResolvedValue(SHIFT);
    getSale.mockResolvedValue(sale);
    completeSale.mockResolvedValue({
      ...sale,
      completed_at: "2026-05-23T08:10:00Z",
      receipt_number: "R-locked",
      status: "completed",
    });

    renderArea();

    await screen.findByText(/Остаток/);
    fireEvent.click(await screen.findByRole("button", { name: /Завершить продажу/i }));
    expect(await screen.findByText(/Чек № R-locked оформлен/i)).toBeInTheDocument();

    const removeItem = vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => {
      throw new DOMException("storage unavailable");
    });
    try {
      fireEvent.click(screen.getByRole("button", { name: /Новая продажа/i }));

      expect(
        await screen.findByText(
          /Не удалось очистить локальное состояние кассы.*Новая продажа не начата/i,
        ),
      ).toBeInTheDocument();
      expect(screen.getByText(/Чек № R-locked оформлен/i)).toBeInTheDocument();
    } finally {
      removeItem.mockRestore();
    }
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
      expect(screen.getByText(/Не удалось подтвердить завершение продажи/i)).toBeInTheDocument(),
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
    fireEvent.keyDown(window, { key: "F2" });

    await waitFor(() => expect(completeSale).toHaveBeenCalledTimes(1));
    expect(requestDesktopCashDrawerOpen).not.toHaveBeenCalled();
    expect(JSON.parse(window.localStorage.getItem(draftKey(REG)) ?? "{}")).toMatchObject({
      saleId: SALE.id,
    });

    resolveComplete({
      ...sale,
      completed_at: "2026-05-23T08:10:00Z",
      receipt_number: "R-3",
      status: "completed",
    });

    await waitFor(() => expect(requestDesktopCashDrawerOpen).toHaveBeenCalledTimes(1));
  });

  it("reuses one operation id after a payment response is lost", async () => {
    seedDraftSale(SALE.id);
    getCurrentShift.mockResolvedValue(SHIFT);
    getSale.mockResolvedValue(SALE);
    addPayment
      .mockRejectedValueOnce(new Error("response lost"))
      .mockResolvedValueOnce({ ...CASH_PAYMENT, id: "pay-retry" });

    renderArea();

    await screen.findByText(/Остаток/);
    fireEvent.keyDown(window, { key: "Enter" });
    await screen.findByText(/Не удалось подтвердить результат оплаты/i);

    fireEvent.keyDown(window, { key: "Enter" });
    await waitFor(() => expect(addPayment).toHaveBeenCalledTimes(2));

    const firstPayload = addPayment.mock.calls[0]?.[1] as { operation_id: string };
    const secondPayload = addPayment.mock.calls[1]?.[1] as { operation_id: string };
    expect(secondPayload.operation_id).toBe(firstPayload.operation_id);
  });

  it("accepts a payment found by reconciliation after its response is lost", async () => {
    seedDraftSale(SALE.id);
    getCurrentShift.mockResolvedValue(SHIFT);
    getSale.mockImplementation(() => {
      const payload = addPayment.mock.calls[0]?.[1] as { operation_id?: string } | undefined;
      if (!payload?.operation_id) return Promise.resolve(SALE);
      return Promise.resolve({
        ...SALE,
        payments: [{ ...CASH_PAYMENT, operation_id: payload.operation_id }],
      });
    });
    addPayment.mockRejectedValue(new Error("response lost"));

    renderArea();

    await screen.findByText(/Остаток/);
    fireEvent.keyDown(window, { key: "Enter" });
    await waitFor(() => expect(getSale).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.getByText(/Остаток/).textContent).toContain("0.00"));
    expect(screen.queryByText(/Не удалось подтвердить результат оплаты/i)).not.toBeInTheDocument();

    fireEvent.keyDown(window, { key: "Enter" });
    await new Promise((resolve) => window.setTimeout(resolve, 0));
    expect(addPayment).toHaveBeenCalledTimes(1);
  });

  it("recovers a completed sale when the completion response is lost", async () => {
    const sale = { ...SALE, payments: [CASH_PAYMENT] };
    const completed = {
      ...sale,
      completed_at: "2026-05-23T08:10:00Z",
      receipt_number: "R-4",
      status: "completed" as const,
    };
    seedDraftSale(sale.id);
    getCurrentShift.mockResolvedValue(SHIFT);
    getSale.mockResolvedValueOnce(sale).mockResolvedValueOnce(completed);
    completeSale.mockRejectedValue(new Error("response lost"));

    renderArea();

    await screen.findByText(/Остаток/);
    fireEvent.click(await screen.findByRole("button", { name: /Завершить продажу/i }));

    await waitFor(() => expect(requestDesktopCashDrawerOpen).toHaveBeenCalledTimes(1));
    expect(await screen.findByText(/Чек № R-4 оформлен/i)).toBeInTheDocument();
    expect(JSON.parse(window.localStorage.getItem(draftKey(REG)) ?? "{}")).toMatchObject({
      saleId: SALE.id,
      status: "completed",
    });
  });

  it("does not abandon an unresolved completion after the page is restored", async () => {
    const sale = { ...SALE, payments: [CASH_PAYMENT] };
    const completed = {
      ...sale,
      completed_at: "2026-05-23T08:10:00Z",
      receipt_number: "R-5",
      status: "completed" as const,
    };
    seedDraftSale(sale.id);
    markPendingCompletion(sale.id);
    getCurrentShift.mockResolvedValue(SHIFT);
    getSale.mockResolvedValue(sale);
    completeSale.mockResolvedValue(completed);

    renderArea();

    await screen.findByText(/Остаток/);
    fireEvent.keyDown(window, { key: "F2" });
    expect(JSON.parse(window.localStorage.getItem(draftKey(REG)) ?? "{}")).toMatchObject({
      saleId: SALE.id,
    });

    fireEvent.keyDown(window, { key: "F4" });
    expect(await screen.findByText(/Чек № R-5 оформлен/i)).toBeInTheDocument();
  });

  it("does not overwrite a completed receipt pointer while the sale is loading", async () => {
    window.localStorage.setItem(
      draftKey(REG),
      JSON.stringify({
        saleId: SALE.id,
        nameById: {},
        savedAt: Date.now(),
        status: "completed",
      }),
    );
    getCurrentShift.mockResolvedValue(SHIFT);
    getSale.mockReturnValue(new Promise(() => undefined));

    const view = renderArea();

    await waitFor(() => expect(getSale).toHaveBeenCalledWith(SALE.id));
    await new Promise((resolve) => window.setTimeout(resolve, 0));
    expect(JSON.parse(window.localStorage.getItem(draftKey(REG)) ?? "{}")).toMatchObject({
      saleId: SALE.id,
      status: "completed",
    });
    view.unmount();
  });

  it("does not send a payment when the recovery state cannot be persisted", async () => {
    seedDraftSale(SALE.id);
    getCurrentShift.mockResolvedValue(SHIFT);
    getSale.mockResolvedValue(SALE);

    renderArea();

    await screen.findByText(/Остаток/);
    const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("storage unavailable");
    });
    fireEvent.keyDown(window, { key: "Enter" });

    expect(
      await screen.findByText(/Локальное хранилище кассы недоступно.*Оплата не отправлена/i),
    ).toBeInTheDocument();
    expect(addPayment).not.toHaveBeenCalled();
    setItem.mockRestore();
  });

  it("surfaces a stored payment payload conflict during reconciliation", async () => {
    seedDraftSale(SALE.id);
    const pending = createPendingPaymentOperation(SALE.id, "card", "20.00");
    if (!pending) throw new Error("pending operation was not persisted");
    getCurrentShift.mockResolvedValue(SHIFT);
    getSale.mockResolvedValue({
      ...SALE,
      payments: [{ ...CASH_PAYMENT, operation_id: pending.operationId }],
    });

    renderArea();

    expect(
      await screen.findByText(/Параметры сохранённой оплаты не совпали с сервером/i),
    ).toBeInTheDocument();
    expect(addPayment).not.toHaveBeenCalled();
  });
});

function seedDraftSale(saleId: string): void {
  window.localStorage.setItem(
    draftKey(REG),
    JSON.stringify({ saleId, nameById: {}, savedAt: Date.now() }),
  );
}
