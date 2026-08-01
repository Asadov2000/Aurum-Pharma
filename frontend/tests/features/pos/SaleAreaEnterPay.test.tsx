import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const getCurrentShift = vi.fn();
const getSale = vi.fn();
const addPayment = vi.fn();
const completeSale = vi.fn();
const checkoutSale = vi.fn();
const getCheckoutResult = vi.fn();
const findByBarcode = vi.fn();
const requestDesktopCashDrawerOpen = vi.fn();

vi.mock("@/features/pos/api", () => ({
  getCurrentShift: (...args: unknown[]) => getCurrentShift(...args),
  getSale: (...args: unknown[]) => getSale(...args),
  addPayment: (...args: unknown[]) => addPayment(...args),
  completeSale: (...args: unknown[]) => completeSale(...args),
  checkoutSale: (...args: unknown[]) => checkoutSale(...args),
  getCheckoutResult: (...args: unknown[]) => getCheckoutResult(...args),
  openShift: vi.fn(),
  closeShift: vi.fn(),
  getZReport: vi.fn(),
  getZReportXlsx: vi.fn(),
  createSale: vi.fn(),
  addSaleItem: vi.fn(),
  updateSaleItem: vi.fn(),
  deleteSaleItem: vi.fn(),
  addPrescription: vi.fn(),
  getReceipt: vi.fn(),
  getReceiptPdf: vi.fn(),
}));

vi.mock("@/features/catalog/queries", () => ({
  useCatalogQuery: () => ({ data: undefined }),
}));

vi.mock("@/features/catalog/api", () => ({
  findByBarcode: (...args: unknown[]) => findByBarcode(...args),
}));

vi.mock("@/lib/desktopBridge", () => ({
  DESKTOP_BARCODE_SCANNED_EVENT: "aurum-desktop-barcode-scanned",
  normalizeDesktopBarcode: (rawCode: string) => rawCode.trim() || null,
  requestDesktopCashDrawerOpen: (...args: unknown[]) => requestDesktopCashDrawerOpen(...args),
}));

import { SaleArea } from "@/features/pos/SaleArea";
import {
  createPendingCheckoutOperation,
  loadPendingCheckoutOperation,
} from "@/features/pos/checkoutOperation";
import { draftKey } from "@/features/pos/draftStorage";
import { createPendingPaymentOperation } from "@/features/pos/paymentOperation";

const REG = "reg-1";
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

interface CheckoutPayload {
  operation_id: string;
  register_id: string;
  draft_sale_id: string;
  items: { catalog_id: string; qty: string }[];
  payments: {
    payment_method: "cash" | "card" | "qr";
    amount: string;
    metadata?: { cash_received?: string; external_confirmed?: true };
  }[];
  prescription?: { patient_name?: string | null };
  expired_sale_confirmed?: boolean;
}

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
  operation_id: null,
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

function checkoutResult(
  operationId: string,
  paymentMethod: "cash" | "card" | "qr" | "bank_transfer" = "cash",
) {
  return {
    event_id: "10000000-0000-4000-8000-000000000001",
    sale_id: SALE.id,
    operation_id: operationId,
    tenant_id: SALE.tenant_id,
    branch_id: SALE.branch_id,
    register_id: REG,
    shift_id: SALE.shift_id,
    cashier_user_id: SALE.cashier_user_id,
    receipt_number: "R-atomic",
    receipt_seq: 1,
    created_at: SALE.created_at,
    completed_at: "2026-05-23T08:10:00Z",
    total_amount: SALE.total_amount,
    currency: SALE.currency,
    is_test: false,
    items: SALE.items.map(({ sale_id: _saleId, ...item }) => item),
    payments: [
      {
        id: "20000000-0000-4000-8000-000000000001",
        payment_method: paymentMethod,
        amount: "50.00",
        currency: "TJS",
      },
    ],
  };
}

function completedSale(
  operationId: string,
  paymentMethod: "cash" | "card" | "qr" | "bank_transfer" = "cash",
) {
  return {
    ...SALE,
    operation_id: operationId,
    status: "completed" as const,
    receipt_number: "R-atomic",
    completed_at: "2026-05-23T08:10:00Z",
    payments: [{ ...CASH_PAYMENT, payment_method: paymentMethod }],
  };
}

function apiError(status: number, detail: string): unknown {
  return {
    isAxiosError: true,
    response: { status, data: { detail } },
  };
}

function renderArea() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <SaleArea
        registerId={REG}
        mode="keyboard"
        soundOn={false}
        draftTtlMin={30}
        paymentMethods={["cash", "card", "qr"]}
        mixedPaymentEnabled
        paymentSettingsLoading={false}
        paymentSettingsUnavailable={false}
      />
    </QueryClientProvider>,
  );
}

function seedDraftSale(saleId: string, requiresRx: boolean = false): void {
  window.localStorage.setItem(
    draftKey(REG),
    JSON.stringify({ saleId, nameById: {}, savedAt: Date.now(), requiresRx }),
  );
}

function localStorageContents(): string {
  return Array.from({ length: window.localStorage.length }, (_value, index) => {
    const key = window.localStorage.key(index);
    return key === null ? "" : (window.localStorage.getItem(key) ?? "");
  }).join("\n");
}

async function stageCashPayment(): Promise<void> {
  await screen.findByText(/Остаток/);
  fireEvent.keyDown(window, { key: "Enter" });
  await screen.findByRole("button", { name: /Сбросить расчёт/i });
}

describe("SaleArea atomic checkout", () => {
  beforeEach(() => {
    window.localStorage.clear();
    getCurrentShift.mockReset();
    getSale.mockReset();
    addPayment.mockReset();
    completeSale.mockReset();
    checkoutSale.mockReset();
    getCheckoutResult.mockReset();
    findByBarcode.mockReset();
    requestDesktopCashDrawerOpen.mockReset();
    getCurrentShift.mockResolvedValue(SHIFT);
    findByBarcode.mockRejectedValue({
      isAxiosError: true,
      response: { status: 404, data: { detail: "not found" } },
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("stages payment in memory and sends one atomic sale command", async () => {
    let operationId = "";
    seedDraftSale(SALE.id);
    getSale
      .mockResolvedValueOnce(SALE)
      .mockImplementation(() => Promise.resolve(completedSale(operationId)));
    checkoutSale.mockImplementation((payload: CheckoutPayload) => {
      operationId = payload.operation_id;
      return Promise.resolve(checkoutResult(operationId));
    });

    renderArea();
    await stageCashPayment();

    expect(addPayment).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: /Завершить продажу/i }));

    await waitFor(() => expect(checkoutSale).toHaveBeenCalledTimes(1));
    const payload = checkoutSale.mock.calls[0]?.[0] as CheckoutPayload;
    expect(payload).toMatchObject({
      operation_id: expect.stringMatching(UUID_PATTERN),
      register_id: REG,
      draft_sale_id: SALE.id,
      items: [{ catalog_id: "c-1", qty: "1.000" }],
      payments: [{ payment_method: "cash", amount: "50.00" }],
    });
    expect(await screen.findByText(/Чек № R-atomic оформлен/i)).toBeInTheDocument();
    expect(requestDesktopCashDrawerOpen).toHaveBeenCalledTimes(1);
    expect(JSON.parse(window.localStorage.getItem(draftKey(REG)) ?? "{}")).toMatchObject({
      saleId: SALE.id,
      status: "completed",
    });
    expect(screen.getByRole("button", { name: /Закрыть смену/i })).toBeEnabled();
  });

  it("resets only the staged cash calculation and keeps the cart intact", async () => {
    seedDraftSale(SALE.id);
    getSale.mockResolvedValue(SALE);

    renderArea();
    await stageCashPayment();
    fireEvent.click(screen.getByRole("button", { name: /Сбросить расчёт/i }));

    await waitFor(() => {
      expect(screen.queryByRole("button", { name: /Сбросить расчёт/i })).not.toBeInTheDocument();
    });
    expect(screen.getByTestId("cart-item")).toBeInTheDocument();
    expect(checkoutSale).not.toHaveBeenCalled();
    expect(addPayment).not.toHaveBeenCalled();
    expect(JSON.parse(window.localStorage.getItem(draftKey(REG)) ?? "{}")).toMatchObject({
      saleId: SALE.id,
      stagedPayments: [],
    });
  });

  it("restores a staged cash calculation with tender and keeps the cart locked", async () => {
    let operationId = "";
    window.localStorage.setItem(
      draftKey(REG),
      JSON.stringify({
        saleId: SALE.id,
        nameById: { "c-1": "Парацетамол" },
        savedAt: Date.now(),
        requiresRx: false,
        stagedPayments: [
          {
            payment_method: "cash",
            amount: "50.00",
            metadata: { cash_received: "100.00" },
          },
        ],
      }),
    );
    getSale
      .mockResolvedValueOnce(SALE)
      .mockImplementation(() => Promise.resolve(completedSale(operationId)));
    checkoutSale.mockImplementation((payload: CheckoutPayload) => {
      operationId = payload.operation_id;
      return Promise.resolve(checkoutResult(operationId));
    });

    renderArea();

    expect(
      await screen.findByRole("button", { name: /Сбросить расчёт/i }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText(/Получено наличными/i)).toHaveValue("100,00");
    expect(screen.queryByRole("button", { name: /Удалить Парацетамол/i })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Завершить продажу/i }));

    await waitFor(() => expect(checkoutSale).toHaveBeenCalledTimes(1));
    expect((checkoutSale.mock.calls[0]?.[0] as CheckoutPayload).payments).toEqual([
      {
        payment_method: "cash",
        amount: "50.00",
        metadata: { cash_received: "100.00" },
      },
    ]);
  });

  it("supports a partial card and QR mixed payment", async () => {
    let operationId = "";
    seedDraftSale(SALE.id);
    getSale
      .mockResolvedValueOnce(SALE)
      .mockImplementation(() => Promise.resolve(completedSale(operationId, "card")));
    checkoutSale.mockImplementation((payload: CheckoutPayload) => {
      operationId = payload.operation_id;
      return Promise.resolve(checkoutResult(operationId, "card"));
    });

    renderArea();
    await screen.findByText(/Остаток/);
    fireEvent.click(screen.getByRole("button", { name: /Карта/i }));
    const cardPad = await screen.findByRole("dialog", { name: "Сумма оплаты" });
    fireEvent.click(within(cardPad).getByRole("button", { name: "2" }));
    fireEvent.click(within(cardPad).getByRole("button", { name: "0" }));
    fireEvent.click(within(cardPad).getByRole("button", { name: "ОК" }));
    const cardConfirmation = await screen.findByRole("dialog", {
      name: "Подтвердить оплату картой",
    });
    expect(
      within(cardConfirmation).getByText(/Aurum не списывает деньги самостоятельно/i),
    ).toBeInTheDocument();
    fireEvent.click(
      within(cardConfirmation).getByRole("button", { name: "Оплата подтверждена" }),
    );

    fireEvent.click(screen.getByRole("button", { name: /Сбросить расчёт/i }));
    const resetDialog = await screen.findByRole("dialog", {
      name: "Сбросить расчёт оплаты",
    });
    expect(within(resetDialog).getByText(/внешнем терминале автоматически не отменится/i)).toBeInTheDocument();
    fireEvent.click(within(resetDialog).getByRole("button", { name: "Отмена" }));

    fireEvent.click(screen.getByRole("button", { name: /QR/i }));
    const qrPad = await screen.findByRole("dialog", { name: "Сумма оплаты" });
    fireEvent.click(within(qrPad).getByRole("button", { name: "ОК" }));
    const qrConfirmation = await screen.findByRole("dialog", {
      name: "Подтвердить оплату QR",
    });
    fireEvent.click(
      within(qrConfirmation).getByRole("button", { name: "Оплата подтверждена" }),
    );
    fireEvent.click(screen.getByRole("button", { name: /Завершить продажу/i }));

    await waitFor(() => expect(checkoutSale).toHaveBeenCalledTimes(1));
    expect((checkoutSale.mock.calls[0]?.[0] as CheckoutPayload).payments).toEqual([
      {
        payment_method: "card",
        amount: "20.00",
        metadata: { external_confirmed: true },
      },
      {
        payment_method: "qr",
        amount: "30.00",
        metadata: { external_confirmed: true },
      },
    ]);
  });

  it("does not stage an external payment until the cashier confirms terminal success", async () => {
    seedDraftSale(SALE.id);
    getSale.mockResolvedValue(SALE);
    renderArea();

    await screen.findByText(/Остаток/);
    fireEvent.click(screen.getByRole("button", { name: /Карта/i }));
    fireEvent.click(
      within(await screen.findByRole("dialog", { name: "Сумма оплаты" })).getByRole("button", {
        name: "ОК",
      }),
    );

    const confirmation = await screen.findByRole("dialog", {
      name: "Подтвердить оплату картой",
    });
    expect(screen.queryByRole("button", { name: /Сбросить расчёт/i })).not.toBeInTheDocument();
    fireEvent.click(within(confirmation).getByRole("button", { name: "Отмена" }));

    expect(
      screen.queryByRole("dialog", { name: "Подтвердить оплату картой" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Сбросить расчёт/i })).not.toBeInTheDocument();
    expect(checkoutSale).not.toHaveBeenCalled();
  });

  it("does not submit or open the drawer twice while checkout is pending", async () => {
    let operationId = "";
    let resolveCheckout: (value: unknown) => void = () => undefined;
    seedDraftSale(SALE.id);
    getSale
      .mockResolvedValueOnce(SALE)
      .mockImplementation(() => Promise.resolve(completedSale(operationId)));
    checkoutSale.mockImplementation(
      (payload: CheckoutPayload) =>
        new Promise((resolve) => {
          operationId = payload.operation_id;
          resolveCheckout = resolve;
        }),
    );

    renderArea();
    await stageCashPayment();
    fireEvent.keyDown(window, { key: "F4" });
    fireEvent.keyDown(window, { key: "F4" });
    fireEvent.keyDown(window, { key: "F2" });

    await waitFor(() => expect(checkoutSale).toHaveBeenCalledTimes(1));
    expect(requestDesktopCashDrawerOpen).not.toHaveBeenCalled();
    expect(JSON.parse(window.localStorage.getItem(draftKey(REG)) ?? "{}")).toMatchObject({
      saleId: SALE.id,
    });

    resolveCheckout(checkoutResult(operationId));
    await waitFor(() => expect(requestDesktopCashDrawerOpen).toHaveBeenCalledTimes(1));
    expect(checkoutSale).toHaveBeenCalledTimes(1);
  });

  it("requires explicit confirmation when expiry is detected at checkout", async () => {
    let operationId = "";
    seedDraftSale(SALE.id);
    getSale
      .mockResolvedValueOnce(SALE)
      .mockImplementation(() => Promise.resolve(completedSale(operationId)));
    checkoutSale
      .mockRejectedValueOnce({
        isAxiosError: true,
        response: {
          status: 422,
          data: {
            error: {
              details: { reason: "expired_sale_confirmation_required" },
            },
          },
        },
      })
      .mockImplementation((payload: CheckoutPayload) => {
        operationId = payload.operation_id;
        return Promise.resolve(checkoutResult(operationId));
      });

    renderArea();
    await stageCashPayment();
    fireEvent.click(screen.getByRole("button", { name: /Завершить продажу/i }));

    const confirmation = await screen.findByRole("dialog", {
      name: "Подтвердить просроченный товар",
    });
    expect(checkoutSale).toHaveBeenCalledTimes(1);
    expect((checkoutSale.mock.calls[0]?.[0] as CheckoutPayload).expired_sale_confirmed).toBe(
      false,
    );
    fireEvent.click(
      within(confirmation).getByRole("button", {
        name: "Подтвердить и продолжить",
      }),
    );

    await waitFor(() => expect(checkoutSale).toHaveBeenCalledTimes(2));
    expect((checkoutSale.mock.calls[1]?.[0] as CheckoutPayload).expired_sale_confirmed).toBe(
      true,
    );
  });

  it("requires confirmation before F2 clears a non-empty draft", async () => {
    seedDraftSale(SALE.id);
    getSale.mockResolvedValue(SALE);

    renderArea();
    await screen.findByText(/Остаток/);

    const closeShift = screen.getByRole("button", { name: /Закрыть смену/i });
    expect(closeShift).toBeDisabled();

    fireEvent.keyDown(window, { key: "F2" });
    expect(screen.getByRole("dialog", { name: "Начать новую продажу" })).toBeInTheDocument();
    expect(window.localStorage.getItem(draftKey(REG))).not.toBeNull();

    fireEvent.click(
      within(screen.getByRole("dialog", { name: "Начать новую продажу" })).getByRole("button", {
        name: "Очистить чек",
      }),
    );

    await waitFor(() => {
      expect(
        screen.queryByRole("dialog", { name: "Начать новую продажу" }),
      ).not.toBeInTheDocument();
    });
    expect(window.localStorage.getItem(draftKey(REG))).toBeNull();
  });

  it("does not open the cash drawer for a card checkout", async () => {
    let operationId = "";
    seedDraftSale(SALE.id);
    getSale
      .mockResolvedValueOnce(SALE)
      .mockImplementation(() => Promise.resolve(completedSale(operationId, "card")));
    checkoutSale.mockImplementation((payload: CheckoutPayload) => {
      operationId = payload.operation_id;
      return Promise.resolve(checkoutResult(operationId, "card"));
    });

    renderArea();
    await screen.findByText(/Остаток/);
    fireEvent.click(screen.getByRole("button", { name: /Карта/i }));
    fireEvent.click(
      within(await screen.findByRole("dialog", { name: "Сумма оплаты" })).getByRole("button", {
        name: "ОК",
      }),
    );
    fireEvent.click(
      within(
        await screen.findByRole("dialog", { name: "Подтвердить оплату картой" }),
      ).getByRole("button", { name: "Оплата подтверждена" }),
    );
    fireEvent.click(screen.getByRole("button", { name: /Завершить продажу/i }));

    await waitFor(() => expect(checkoutSale).toHaveBeenCalledTimes(1));
    expect(requestDesktopCashDrawerOpen).not.toHaveBeenCalled();
  });

  it("does not replace a focused payment button action with the cash shortcut", async () => {
    seedDraftSale(SALE.id);
    getSale.mockResolvedValue(SALE);
    renderArea();

    await screen.findByText(/Остаток/);
    const cardButton = await screen.findByRole("button", { name: /Карта/i });
    expect(screen.getByRole("region", { name: "Краткая сумма чека" })).toHaveTextContent(
      "50.00 TJS",
    );
    cardButton.focus();
    fireEvent.keyDown(cardButton, { key: "Enter" });

    expect(screen.queryByRole("button", { name: /Сбросить расчёт/i })).not.toBeInTheDocument();
  });

  it("does not treat the barcode scanner terminator as a cash-payment shortcut", async () => {
    seedDraftSale(SALE.id);
    getSale.mockResolvedValue(SALE);
    renderArea();

    await screen.findByText(/Остаток/);
    const scannerSink = document.querySelector<HTMLInputElement>('[data-barcode-sink="true"]');
    expect(scannerSink).not.toBeNull();
    scannerSink?.focus();
    for (const key of "1234567890123") {
      fireEvent.keyDown(window, { key });
    }
    fireEvent.keyDown(window, { key: "Enter" });

    await screen.findByText(/Штрихкод 1234567890123 не найден/i);
    expect(screen.queryByRole("button", { name: /Сбросить расчёт/i })).not.toBeInTheDocument();
    expect(requestDesktopCashDrawerOpen).not.toHaveBeenCalled();
  });

  it("hides the mobile payment shortcut when the payment panel is visible", async () => {
    let observerCallback: IntersectionObserverCallback = () => undefined;
    class TestIntersectionObserver implements IntersectionObserver {
      readonly root = null;
      readonly rootMargin = "0px";
      readonly thresholds = [0.35];

      constructor(callback: IntersectionObserverCallback) {
        observerCallback = callback;
      }

      disconnect(): void {}
      observe(): void {}
      takeRecords(): IntersectionObserverEntry[] {
        return [];
      }
      unobserve(): void {}
    }
    vi.stubGlobal("IntersectionObserver", TestIntersectionObserver);
    seedDraftSale(SALE.id);
    getSale.mockResolvedValue(SALE);
    renderArea();

    await screen.findByText(/Остаток/);
    expect(screen.getByRole("region", { name: "Краткая сумма чека" })).toBeInTheDocument();

    act(() => {
      observerCallback(
        [{ isIntersecting: true, intersectionRatio: 0.1 } as IntersectionObserverEntry],
        {} as IntersectionObserver,
      );
    });
    expect(screen.getByRole("region", { name: "Краткая сумма чека" })).toBeInTheDocument();

    act(() => {
      observerCallback(
        [{ isIntersecting: true, intersectionRatio: 0.5 } as IntersectionObserverEntry],
        {} as IntersectionObserver,
      );
    });

    expect(screen.queryByRole("region", { name: "Краткая сумма чека" })).not.toBeInTheDocument();
  });

  it("recovers a lost checkout response by the same operation id", async () => {
    let recoveredOperationId = "";
    seedDraftSale(SALE.id);
    getSale
      .mockResolvedValueOnce(SALE)
      .mockImplementation(() => Promise.resolve(completedSale(recoveredOperationId)));
    checkoutSale.mockRejectedValue(new Error("response lost"));
    getCheckoutResult.mockImplementation((operationId: string) => {
      recoveredOperationId = operationId;
      return Promise.resolve(checkoutResult(operationId));
    });

    renderArea();
    await stageCashPayment();
    fireEvent.click(screen.getByRole("button", { name: /Завершить продажу/i }));

    await waitFor(() => expect(getCheckoutResult).toHaveBeenCalledTimes(1));
    const payload = checkoutSale.mock.calls[0]?.[0] as CheckoutPayload;
    expect(getCheckoutResult).toHaveBeenCalledWith(payload.operation_id);
    expect(checkoutSale).toHaveBeenCalledTimes(1);
    expect(await screen.findByText(/Чек № R-atomic оформлен/i)).toBeInTheDocument();
    expect(requestDesktopCashDrawerOpen).toHaveBeenCalledTimes(1);
    expect(loadPendingCheckoutOperation(SALE.id, REG)).toBeNull();
  });

  it("recovers a committed checkout after page restoration without reopening the drawer", async () => {
    seedDraftSale(SALE.id);
    const operation = createPendingCheckoutOperation(SALE.id, REG);
    if (!operation) throw new Error("pending checkout was not persisted");
    expect(loadPendingCheckoutOperation(SALE.id, REG)).toEqual(operation);
    getSale
      .mockResolvedValueOnce(SALE)
      .mockImplementation(() => Promise.resolve(completedSale(operation.operationId)));
    getCheckoutResult.mockResolvedValue(checkoutResult(operation.operationId));

    renderArea();

    await waitFor(() => expect(getCheckoutResult).toHaveBeenCalledWith(operation.operationId));
    expect(await screen.findByText(/Чек № R-atomic оформлен/i)).toBeInTheDocument();
    expect(getCheckoutResult).toHaveBeenCalledWith(operation.operationId);
    expect(checkoutSale).not.toHaveBeenCalled();
    expect(requestDesktopCashDrawerOpen).not.toHaveBeenCalled();
    expect(loadPendingCheckoutOperation(SALE.id, REG)).toBeNull();
  });

  it("keeps an unresolved marker and blocks a new sale when reconciliation is unavailable", async () => {
    seedDraftSale(SALE.id);
    const operation = createPendingCheckoutOperation(SALE.id, REG);
    if (!operation) throw new Error("pending checkout was not persisted");
    getSale.mockResolvedValue(SALE);
    getCheckoutResult.mockRejectedValue(new Error("offline"));

    renderArea();

    expect(
      await screen.findByText(/Не удалось проверить результат продажи.*Не повторяйте оплату/i),
    ).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "F2" });
    expect(loadPendingCheckoutOperation(SALE.id, REG)).toEqual(operation);
    expect(JSON.parse(window.localStorage.getItem(draftKey(REG)) ?? "{}")).toMatchObject({
      saleId: SALE.id,
    });
  });

  it("retries an ambiguous checkout with the same operation id after a temporary 404", async () => {
    let completedOperationId = "";
    seedDraftSale(SALE.id);
    getSale.mockImplementation(() =>
      Promise.resolve(
        completedOperationId ? completedSale(completedOperationId) : SALE,
      ),
    );
    checkoutSale
      .mockRejectedValueOnce(new Error("response lost"))
      .mockImplementation((payload: CheckoutPayload) => {
        completedOperationId = payload.operation_id;
        return Promise.resolve(checkoutResult(payload.operation_id));
      });
    getCheckoutResult.mockRejectedValue(apiError(404, "not committed yet"));

    renderArea();
    await stageCashPayment();
    fireEvent.click(screen.getByRole("button", { name: /Завершить продажу/i }));

    await waitFor(() => expect(getCheckoutResult).toHaveBeenCalledTimes(1));
    const firstPayload = checkoutSale.mock.calls[0]?.[0] as CheckoutPayload;
    expect(loadPendingCheckoutOperation(SALE.id, REG)?.operationId).toBe(
      firstPayload.operation_id,
    );

    fireEvent.click(screen.getByRole("button", { name: /Завершить продажу/i }));

    await waitFor(() => expect(checkoutSale).toHaveBeenCalledTimes(2));
    const retriedPayload = checkoutSale.mock.calls[1]?.[0] as CheckoutPayload;
    expect(retriedPayload.operation_id).toBe(firstPayload.operation_id);
    expect(await screen.findByText(/Чек № R-atomic оформлен/i)).toBeInTheDocument();
    expect(loadPendingCheckoutOperation(SALE.id, REG)).toBeNull();
  });

  it("clears the operation marker after a definite server rejection", async () => {
    seedDraftSale(SALE.id);
    getSale.mockResolvedValue(SALE);
    checkoutSale.mockRejectedValue(apiError(422, "Недостаточно товара"));

    renderArea();
    await stageCashPayment();
    fireEvent.click(screen.getByRole("button", { name: /Завершить продажу/i }));

    expect(await screen.findByText(/Не удалось оформить продажу/i)).toBeInTheDocument();
    expect(getCheckoutResult).not.toHaveBeenCalled();
    expect(loadPendingCheckoutOperation(SALE.id, REG)).toBeNull();
    expect(screen.getByRole("button", { name: /Сбросить расчёт/i })).toBeInTheDocument();
  });

  it("does not send checkout when its recovery marker cannot be persisted", async () => {
    seedDraftSale(SALE.id);
    getSale.mockResolvedValue(SALE);
    renderArea();
    await stageCashPayment();

    const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("storage unavailable");
    });
    try {
      fireEvent.click(screen.getByRole("button", { name: /Завершить продажу/i }));
      expect(
        await screen.findByText(/Локальное хранилище кассы недоступно.*Продажа не отправлена/i),
      ).toBeInTheDocument();
      expect(checkoutSale).not.toHaveBeenCalled();
    } finally {
      setItem.mockRestore();
    }
  });

  it("keeps prescription PII out of localStorage and sends it only in checkout", async () => {
    let operationId = "";
    seedDraftSale(SALE.id, true);
    getSale
      .mockResolvedValueOnce(SALE)
      .mockImplementation(() => Promise.resolve(completedSale(operationId)));
    checkoutSale.mockImplementation((payload: CheckoutPayload) => {
      operationId = payload.operation_id;
      return Promise.resolve(checkoutResult(operationId));
    });

    renderArea();
    await stageCashPayment();
    fireEvent.click(screen.getByRole("button", { name: /Завершить продажу/i }));
    fireEvent.change(await screen.findByLabelText(/Пациент/i), {
      target: { value: "Иван Иванов" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Сохранить/i }));

    await waitFor(() => expect(screen.queryByLabelText(/Пациент/i)).not.toBeInTheDocument());
    expect(localStorageContents()).not.toContain("Иван Иванов");
    fireEvent.click(screen.getByRole("button", { name: /Завершить продажу/i }));

    await waitFor(() => expect(checkoutSale).toHaveBeenCalledTimes(1));
    const payload = checkoutSale.mock.calls[0]?.[0] as CheckoutPayload;
    expect(payload.prescription?.patient_name).toBe("Иван Иванов");
    expect(localStorageContents()).not.toContain("Иван Иванов");
  });

  it("finishes a legacy draft that already contains a recorded payment", async () => {
    const legacyDraft = { ...SALE, payments: [CASH_PAYMENT] };
    const completed = completedSale("30000000-0000-4000-8000-000000000001");
    seedDraftSale(SALE.id);
    getSale.mockResolvedValue(legacyDraft);
    completeSale.mockResolvedValue(completed);

    renderArea();
    await screen.findByText(/Остаток/);
    fireEvent.click(screen.getByRole("button", { name: /Завершить продажу/i }));

    await waitFor(() => expect(completeSale).toHaveBeenCalledWith(SALE.id, false));
    expect(checkoutSale).not.toHaveBeenCalled();
    expect(requestDesktopCashDrawerOpen).toHaveBeenCalledTimes(1);
  });

  it("surfaces a stored legacy payment payload conflict during reconciliation", async () => {
    seedDraftSale(SALE.id);
    const pending = createPendingPaymentOperation(SALE.id, "card", "20.00", {
      external_confirmed: true,
    });
    if (!pending) throw new Error("pending payment was not persisted");
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
