import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SaleArea } from "@/features/pos/SaleArea";
import { draftKey } from "@/features/pos/draftStorage";
import { type PhysicalCartItem } from "@/features/pos/physicalCart";
import { type PhysicalCashCheckoutResult } from "@/features/pos/physicalCashCheckout";
import {
  physicalCashRecoveryKey,
  mixedCashRecoveryKey,
} from "@/features/pos/physicalCashRecoveryPresence";
import {
  loadPhysicalCartRecovery,
  type PhysicalCartRecovery,
} from "@/features/pos/physicalCartRecovery";
import { posKeys } from "@/features/pos/queries";
import { type SaleDetails, type Shift } from "@/features/pos/types";
import { useAuthStore } from "@/stores/auth";

import { authenticatePos, POS_SCOPE } from "./posIdentityFixture";

const calls = vi.hoisted(() => ({
  sale: vi.fn(),
  shift: vi.fn(),
  addPayment: vi.fn(),
  checkout: vi.fn(),
  complete: vi.fn(),
  create: vi.fn(),
  addItem: vi.fn(),
  barcode: vi.fn(),
  attempt: vi.fn(),
  editor: vi.fn(),
  panel: vi.fn(),
  drawer: vi.fn(),
  catalog: vi.fn(),
  prescription: vi.fn(),
}));
vi.mock("@tanstack/react-router", async (original) => ({
  ...(await original<typeof import("@tanstack/react-router")>()),
  useNavigate: () => vi.fn(),
}));
vi.mock("@/features/settings/useQuickActionPreferences", () => ({
  useQuickActionPreferences: () => ({
    config: { enabled: false, order: [], hidden: [] },
    scope: "test",
  }),
}));
vi.mock("@/features/pos/api", () => ({
  getCurrentShift: calls.shift,
  getSale: calls.sale,
  addPayment: calls.addPayment,
  completeSale: calls.complete,
  checkoutSale: calls.checkout,
  getCheckoutResult: vi.fn(),
  openShift: vi.fn(),
  closeShift: vi.fn(),
  getZReport: vi.fn(),
  getZReportXlsx: vi.fn(),
  createSale: calls.create,
  addSaleItem: calls.addItem,
  updateSaleItem: vi.fn(),
  deleteSaleItem: vi.fn(),
  addPrescription: calls.prescription,
  getReceipt: vi.fn(),
  getReceiptPdf: vi.fn(),
  getPosFavorites: vi.fn(async () => []),
  addPosFavorite: vi.fn(),
  removePosFavorite: vi.fn(),
  createPaymentAttempt: calls.attempt,
  getActivePaymentAttempt: vi.fn(async () => null),
  beginPaymentAttemptReconciliation: vi.fn(),
  confirmPaymentAttempt: vi.fn(),
  voidPaymentAttempt: vi.fn(),
}));
vi.mock("@/features/catalog/pickerQueries", () => ({
  useCatalogPickerQuery: () => ({ data: undefined }),
}));
vi.mock("@/features/catalog/api", () => ({
  findByBarcode: calls.barcode,
  getCatalogItem: calls.catalog,
}));
vi.mock("@/features/pos/PrescriptionModal", () => ({
  PrescriptionModal: (props: { onSaved: (payload: { prescription_number: string }) => void }) => (
    <button onClick={() => props.onSaved({ prescription_number: "RX-test" })}>
      Сохранить тестовый рецепт
    </button>
  ),
}));
vi.mock("@/lib/desktopBridge", () => ({
  DESKTOP_BARCODE_SCANNED_EVENT: "aurum-desktop-barcode-scanned",
  normalizeDesktopBarcode: (raw: string) => raw.trim() || null,
  requestDesktopCashDrawerOpen: calls.drawer,
}));
vi.mock("@/features/pos/usePosRegisterLock", () => ({
  usePosRegisterLock: () => ({ status: "owned", isOwner: true, message: null }),
}));
vi.mock("@/features/pos/PhysicalCartEditor", () => ({
  PhysicalCartEditor: (props: {
    items: PhysicalCartItem[];
    disabled: boolean;
    onChanged: () => Promise<void>;
  }) => {
    calls.editor(props);
    return (
      <section aria-label="Физическая корзина">
        <p>{props.items.length} физических строк</p>
        <button disabled={props.disabled} onClick={() => void props.onChanged()}>
          Обновить физическую корзину
        </button>
      </section>
    );
  },
}));
vi.mock("@/features/pos/PhysicalCashPanel", () => ({
  PhysicalCashPanel: (props: { canPay: boolean; onBlockedChange: (blocked: boolean) => void }) => {
    calls.panel(props);
    return (
      <section aria-label="Оплата наличными">
        {props.canPay ? "Оплата доступна" : "Восстановление оплаты"}
      </section>
    );
  },
}));

const shift: Shift = {
  id: "sh-1",
  tenant_id: "t-1",
  branch_id: "b-1",
  register_id: "reg-1",
  opened_by_user_id: "u-1",
  closed_by_user_id: null,
  opened_at: "2026-10-08T08:00:00Z",
  closed_at: null,
  status: "open",
  opening_cash: "100.00",
  closing_cash_actual: null,
  closing_cash_expected: null,
  closing_difference: null,
  totals: null,
  currency: "TJS",
  notes: null,
};
const physicalItem: PhysicalCartItem = {
  id: "physical-1",
  tenant_id: "t-1",
  branch_id: "b-1",
  sale_id: "sale-1",
  catalog_id: "c-1",
  position: 1,
  sale_format: "unit",
  quantity: 2,
  discount_amount: "0.00",
  physical_packaging: {
    version: 2,
    unit: "tablet",
    units_per_pack: 30,
    units_per_blister: 10,
    dispense_step: 1,
  },
};
const legacyItem: SaleDetails["items"][number] = {
  id: "legacy-1",
  sale_id: "sale-1",
  catalog_id: "legacy-catalog",
  batch_id: "batch-1",
  qty: "1",
  unit_price: "10.00",
  total_price: "10.00",
  currency: "TJS",
  discount_amount: "0.00",
  position: 1,
};
function sale(overrides: Partial<SaleDetails> = {}): SaleDetails {
  return {
    id: "sale-1",
    tenant_id: "t-1",
    branch_id: "b-1",
    register_id: "reg-1",
    shift_id: "sh-1",
    sale_type: "sale",
    parent_sale_id: null,
    status: "draft",
    receipt_number: null,
    operation_id: null,
    is_test: false,
    total_amount: "50.00",
    currency: "TJS",
    voided_at: null,
    voided_by_sale_id: null,
    cashier_user_id: "u-1",
    created_at: "2026-10-08T08:05:00Z",
    completed_at: null,
    items: [],
    payments: [],
    prepared_cart_items: [physicalItem],
    ...overrides,
  };
}
function physicalResult(
  overrides: Partial<PhysicalCashCheckoutResult> = {},
): PhysicalCashCheckoutResult {
  return {
    schema_version: 3,
    document_kind: "physical_checkout",
    tenant_id: "t-1",
    branch_id: "b-1",
    sale_id: "sale-1",
    register_id: "reg-1",
    shift_id: "sh-1",
    cashier_id: "u-1",
    operation_id: "operation-1",
    event_id: "event-1",
    stock_command_id: "stock-command-1",
    created_at: "2026-10-08T08:05:00Z",
    completed_at: "2026-10-08T08:06:00Z",
    receipt_number: "R-1",
    receipt_sequence: 1,
    is_test: false,
    lines: [
      {
        inventory_kind: "physical_unit",
        id: physicalItem.id,
        catalog_id: physicalItem.catalog_id,
        position: 1,
        name: "Аспирин",
        packaging: physicalItem.physical_packaging!,
        sale_format: "unit",
        quantity: 2,
        base_units: 2,
        original_retail_unit_price: "25.00",
        gross_amount: "50.00",
        discount_amount: "0.00",
        net_amount: "50.00",
        money_version: 1,
        currency: "TJS",
      },
    ],
    payments: [{ id: "operation-1", method: "cash", amount: "50.00", currency: "TJS" }],
    total_amount: "50.00",
    currency: "TJS",
    ...overrides,
  };
}
type CashPanelCallbacks = {
  onBlockedChange: (blocked: boolean) => void;
  onCompleted: (result: PhysicalCashCheckoutResult, openDrawer: boolean) => Promise<boolean>;
};
async function cashPanelCallbacks(): Promise<CashPanelCallbacks> {
  await screen.findByRole("region", { name: "Оплата наличными" });
  return calls.panel.mock.calls.at(-1)?.[0] as CashPanelCallbacks;
}
function completedSale(overrides: Partial<SaleDetails> = {}): SaleDetails {
  return sale({
    status: "completed",
    receipt_number: "R-1",
    operation_id: "draft-create-operation",
    payments: [
      {
        id: "operation-1",
        operation_id: "operation-1",
        sale_id: "sale-1",
        payment_method: "cash",
        amount: "50.00",
        currency: "TJS",
      },
    ],
    completed_at: "2026-10-08T08:06:00Z",
    ...overrides,
  });
}
function mount(mode: "keyboard" | "touch" = "keyboard") {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const view = render(
    <QueryClientProvider client={client}>
      <SaleArea
        registerId="reg-1"
        mode={mode}
        soundOn={false}
        draftTtlMin={30}
        paymentMethods={["cash", "card", "qr"]}
        mixedPaymentEnabled
        paymentSettingsLoading={false}
        paymentSettingsUnavailable={false}
      />
    </QueryClientProvider>,
  );
  return { ...view, client };
}
describe("SaleArea physical cart integration", () => {
  beforeEach(() => {
    authenticatePos();
    window.localStorage.clear();
    vi.clearAllMocks();
    calls.shift.mockResolvedValue(shift);
    calls.sale.mockResolvedValue(sale());
    window.localStorage.setItem(
      draftKey("reg-1", POS_SCOPE),
      JSON.stringify({ saleId: "sale-1", nameById: { "c-1": "Аспирин" }, savedAt: Date.now() }),
    );
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it.each(["keyboard", "touch"] as const)(
    "restores physical rows and prevents legacy payment entry points in %s mode",
    async (mode) => {
      mount(mode);
      await screen.findByRole("region", { name: "Физическая корзина" });
      expect(await screen.findByRole("region", { name: "Оплата наличными" })).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Наличные" })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Карта" })).not.toBeInTheDocument();
      expect(screen.queryByRole("region", { name: "Быстрая оплата" })).not.toBeInTheDocument();
      for (const key of ["Enter", "F4", "F2"]) fireEvent.keyDown(window, { key });
      expect(screen.getByRole("region", { name: "Физическая корзина" })).toBeInTheDocument();
      expect(calls.addPayment).not.toHaveBeenCalled();
      expect(calls.checkout).not.toHaveBeenCalled();
      expect(calls.complete).not.toHaveBeenCalled();
      expect(calls.attempt).not.toHaveBeenCalled();
      expect(calls.create).not.toHaveBeenCalled();
    },
  );

  it("does not scan legacy stock into a restored physical cart", async () => {
    mount();
    await screen.findByRole("region", { name: "Физическая корзина" });
    fireEvent(
      window,
      new CustomEvent("aurum-desktop-barcode-scanned", { detail: { code: "1234567890123" } }),
    );
    await act(async () => undefined);
    expect(calls.barcode).not.toHaveBeenCalled();
    expect(calls.addItem).not.toHaveBeenCalled();
  });

  it("locks editing and register switching immediately when cash begins", async () => {
    mount();
    await screen.findByRole("region", { name: "Оплата наличными" });
    const panel = calls.panel.mock.calls.at(-1)?.[0] as {
      onBlockedChange: (blocked: boolean) => void;
    };
    act(() => panel.onBlockedChange(true));
    expect(screen.getByRole("button", { name: "Обновить физическую корзину" })).toBeDisabled();
    fireEvent.keyDown(window, { key: "F2" });
    expect(calls.create).not.toHaveBeenCalled();
  });

  it("mounts recovery for a completed sale even after the shift closes", async () => {
    calls.shift.mockResolvedValue(null);
    calls.sale.mockResolvedValue(sale({ status: "completed", receipt_number: "R-1" }));
    localStorage.setItem(physicalCashRecoveryKey("sale-1", POS_SCOPE), "unresolved");
    mount();
    expect(await screen.findByText("Восстановление оплаты")).toBeInTheDocument();
    expect(calls.panel.mock.calls.at(-1)?.[0].canPay).toBe(false);
    expect(screen.queryByRole("region", { name: "Физическая корзина" })).not.toBeInTheDocument();
  });

  it("keeps the completed receipt visible after closed-shift recovery is acknowledged", async () => {
    calls.shift.mockResolvedValue(null);
    calls.sale.mockResolvedValue(completedSale());
    const recoveryKey = physicalCashRecoveryKey("sale-1", POS_SCOPE);
    localStorage.setItem(recoveryKey, "pending-original-operation");
    mount();
    const panel = await cashPanelCallbacks();
    let accepted = false;
    await act(async () => {
      accepted = await panel.onCompleted(physicalResult(), false);
    });
    expect(accepted).toBe(true);
    act(() => {
      localStorage.removeItem(recoveryKey);
      panel.onBlockedChange(false);
    });
    expect(await screen.findByText("Чек № R-1 оформлен")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Печать чека" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Оплата наличными" })).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Физическая корзина" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Новая продажа/ })).not.toBeInTheDocument();
    fireEvent.keyDown(window, { key: "F2" });
    expect(calls.create).not.toHaveBeenCalled();
    expect(screen.getByText("Чек № R-1 оформлен")).toBeInTheDocument();
  });

  it("restores a saved completed receipt without a pending operation or open shift", async () => {
    calls.shift.mockResolvedValue(null);
    calls.sale.mockResolvedValue(completedSale());
    localStorage.setItem(
      draftKey("reg-1", POS_SCOPE),
      JSON.stringify({
        saleId: "sale-1",
        status: "completed",
        nameById: { "c-1": "Аспирин" },
        savedAt: Date.now(),
      }),
    );
    mount();
    expect(await screen.findByText("Чек № R-1 оформлен")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Печать чека" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Оплата наличными" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Новая продажа/ })).not.toBeInTheDocument();
    fireEvent.keyDown(window, { key: "F2" });
    fireEvent.keyDown(window, { key: "Enter" });
    expect(calls.create).not.toHaveBeenCalled();
    expect(calls.addItem).not.toHaveBeenCalled();
    expect(calls.addPayment).not.toHaveBeenCalled();
  });

  it("confirms and persists the completed physical receipt before accepting payment", async () => {
    mount();
    const panel = await cashPanelCallbacks();
    act(() => panel.onBlockedChange(true));
    calls.sale.mockResolvedValue(completedSale());
    let accepted = false;
    await act(async () => {
      accepted = await panel.onCompleted(physicalResult(), true);
    });
    expect(accepted).toBe(true);
    expect(JSON.parse(localStorage.getItem(draftKey("reg-1", POS_SCOPE)) ?? "null")).toMatchObject({
      saleId: "sale-1",
      status: "completed",
    });
    expect(calls.drawer).toHaveBeenCalledExactlyOnceWith({
      reason: "sale-completed",
      registerId: "reg-1",
      saleId: "sale-1",
    });
    await act(async () => {
      expect(await panel.onCompleted(physicalResult(), true)).toBe(true);
    });
    expect(calls.drawer).toHaveBeenCalledTimes(1);
  });

  it("keeps cash recovery blocked when the completed receipt cannot be persisted", async () => {
    mount();
    const panel = await cashPanelCallbacks();
    act(() => panel.onBlockedChange(true));
    const key = physicalCashRecoveryKey("sale-1", POS_SCOPE);
    localStorage.setItem(key, "pending-original-operation");
    const originalSetItem = Storage.prototype.setItem;
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(function (key, value) {
      if (key === draftKey("reg-1", POS_SCOPE))
        throw new DOMException("Full", "QuotaExceededError");
      originalSetItem.call(this, key, value);
    });
    calls.sale.mockResolvedValue(completedSale());
    let accepted = true;
    await act(async () => {
      accepted = await panel.onCompleted(physicalResult(), true);
    });
    expect(accepted).toBe(false);
    expect(localStorage.getItem(key)).toBe("pending-original-operation");
    expect(
      await screen.findByText(/Чек завершён, но не удалось сохранить ссылку/),
    ).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Оплата наличными" })).toBeInTheDocument();
    expect(calls.drawer).not.toHaveBeenCalled();
  });

  it.each(["tenant_id", "cashier_id", "register_id", "sale_id"] as const)(
    "rejects a physical cash result with another %s before refetch or receipt persistence",
    async (field) => {
      mount();
      const panel = await cashPanelCallbacks();
      const before = calls.sale.mock.calls.length;
      const storedDraft = localStorage.getItem(draftKey("reg-1", POS_SCOPE));
      await act(async () => {
        expect(await panel.onCompleted(physicalResult({ [field]: "other" }), true)).toBe(false);
      });
      expect(calls.sale).toHaveBeenCalledTimes(before);
      expect(localStorage.getItem(draftKey("reg-1", POS_SCOPE))).toBe(storedDraft);
      expect(calls.drawer).not.toHaveBeenCalled();
    },
  );

  it.each([
    "branch_id",
    "shift_id",
    "receipt_number",
    "total_amount",
    "tenant_id",
    "cashier_user_id",
    "currency",
  ] as const)("does not accept a result when the server receipt disagrees on %s", async (field) => {
    mount();
    const panel = await cashPanelCallbacks();
    act(() => panel.onBlockedChange(true));
    const key = physicalCashRecoveryKey("sale-1", POS_SCOPE);
    localStorage.setItem(key, "pending-original-operation");
    calls.sale.mockResolvedValue(
      completedSale({ [field]: field === "total_amount" ? "51.00" : "other" }),
    );
    await act(async () => {
      await expect(panel.onCompleted(physicalResult(), true)).rejects.toThrow(
        "Не удалось подтвердить завершённый чек",
      );
    });
    expect(localStorage.getItem(key)).toBe("pending-original-operation");
    expect(screen.getByRole("region", { name: "Оплата наличными" })).toBeInTheDocument();
    expect(calls.drawer).not.toHaveBeenCalled();
  });

  it.each(["id", "operation_id", "sale_id", "payment_method", "amount", "currency"] as const)(
    "does not accept a receipt whose payment disagrees on %s",
    async (field) => {
      mount();
      const panel = await cashPanelCallbacks();
      act(() => panel.onBlockedChange(true));
      const key = physicalCashRecoveryKey("sale-1", POS_SCOPE);
      localStorage.setItem(key, "pending-original-operation");
      const paid = completedSale();
      const payment = paid.payments[0]!;
      calls.sale.mockResolvedValue(
        completedSale({
          payments: [
            {
              ...payment,
              [field]: field === "amount" ? "51.00" : field === "payment_method" ? "card" : "other",
            },
          ],
        }),
      );
      await act(async () => {
        await expect(panel.onCompleted(physicalResult(), true)).rejects.toThrow(
          "Не удалось подтвердить завершённый чек",
        );
      });
      expect(localStorage.getItem(key)).toBe("pending-original-operation");
      expect(screen.getByRole("region", { name: "Оплата наличными" })).toBeInTheDocument();
      expect(calls.drawer).not.toHaveBeenCalled();
    },
  );

  it.each(["missing", "extra"] as const)(
    "does not accept a completed receipt with a %s payment",
    async (kind) => {
      mount();
      const panel = await cashPanelCallbacks();
      act(() => panel.onBlockedChange(true));
      const key = physicalCashRecoveryKey("sale-1", POS_SCOPE);
      localStorage.setItem(key, "pending-original-operation");
      const paid = completedSale();
      calls.sale.mockResolvedValue(
        completedSale({
          payments:
            kind === "missing"
              ? []
              : [
                  ...paid.payments,
                  {
                    ...paid.payments[0]!,
                    id: "another-payment",
                    operation_id: "another-operation",
                  },
                ],
        }),
      );
      await act(async () => {
        await expect(panel.onCompleted(physicalResult(), true)).rejects.toThrow(
          "Не удалось подтвердить завершённый чек",
        );
      });
      expect(localStorage.getItem(key)).toBe("pending-original-operation");
      expect(calls.drawer).not.toHaveBeenCalled();
    },
  );

  it("does not accept the old cashier's receipt if the account changes during verification", async () => {
    mount();
    const panel = await cashPanelCallbacks();
    const key = physicalCashRecoveryKey("sale-1", POS_SCOPE);
    localStorage.setItem(key, "pending-original-operation");
    const originalDraft = localStorage.getItem(draftKey("reg-1", POS_SCOPE));
    let resolveSale: (value: SaleDetails) => void = () => {
      throw new Error("Verification has not started");
    };
    calls.sale.mockImplementationOnce(
      () =>
        new Promise<SaleDetails>((resolve) => {
          resolveSale = resolve;
        }),
    );
    let verification: Promise<boolean>;
    act(() => {
      verification = panel.onCompleted(physicalResult(), true);
    });
    act(() => authenticatePos({ tenantId: "t-1", userId: "u-2" }));
    let accepted = true;
    await act(async () => {
      resolveSale(completedSale());
      accepted = await verification;
    });
    expect(accepted).toBe(false);
    expect(localStorage.getItem(key)).toBe("pending-original-operation");
    expect(localStorage.getItem(draftKey("reg-1", POS_SCOPE))).toBe(originalDraft);
    expect(calls.drawer).not.toHaveBeenCalled();
  });

  it("enables the mixed cash panel while keeping old payment blocked", async () => {
    calls.sale.mockResolvedValue(
      sale({
        items: [
          {
            id: "item-1",
            sale_id: "sale-1",
            catalog_id: "c-1",
            batch_id: "batch-1",
            qty: "1",
            unit_price: "50.00",
            total_price: "50.00",
            currency: "TJS",
            discount_amount: "0",
            position: 1,
          },
        ],
      }),
    );
    mount();
    await screen.findByRole("region", { name: "Физическая корзина" });
    expect(
      screen.getByText("Обычные и поштучные позиции оплачиваются вместе наличными."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Обновить физическую корзину" })).toBeEnabled();
    expect(calls.panel).toHaveBeenLastCalledWith(
      expect.objectContaining({
        kind: "mixed",
        canPay: true,
        legacyItems: [expect.objectContaining({ id: "item-1" })],
      }),
    );
    expect(screen.queryByRole("button", { name: "Увеличить количество" })).not.toBeInTheDocument();
    fireEvent.keyDown(window, { key: "F4" });
    expect(calls.checkout).not.toHaveBeenCalled();
    expect(calls.complete).not.toHaveBeenCalled();
  });

  it("refetches persisted physical rows through the existing sale query", async () => {
    mount();
    await screen.findByRole("region", { name: "Физическая корзина" });
    calls.sale.mockResolvedValue(
      sale({
        prepared_cart_items: [physicalItem, { ...physicalItem, id: "physical-2", position: 2 }],
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Обновить физическую корзину" }));
    expect(await screen.findByText("2 физических строк")).toBeInTheDocument();
  });
  it("restores the saved channel before considering the changed live cart", async () => {
    localStorage.setItem(physicalCashRecoveryKey("sale-1", POS_SCOPE), "uncertain-physical");
    calls.sale.mockResolvedValue(sale({ items: [legacyItem] }));
    mount();
    await cashPanelCallbacks();
    expect(calls.panel).toHaveBeenLastCalledWith(expect.objectContaining({ kind: "physical" }));
    expect(localStorage.getItem(physicalCashRecoveryKey("sale-1", POS_SCOPE))).toBe(
      "uncertain-physical",
    );
  });
  it("restores the mixed channel when the completed sale no longer has draft rows", async () => {
    localStorage.setItem(mixedCashRecoveryKey("sale-1", POS_SCOPE), "uncertain-mixed");
    calls.sale.mockResolvedValue(completedSale({ prepared_cart_items: [] }));
    calls.shift.mockResolvedValue(null);
    mount();
    await cashPanelCallbacks();
    expect(calls.panel).toHaveBeenLastCalledWith(
      expect.objectContaining({ kind: "mixed", canPay: false }),
    );
    expect(localStorage.getItem(mixedCashRecoveryKey("sale-1", POS_SCOPE))).toBe("uncertain-mixed");
  });
  it("opens the physical editor after legacy lines without making a second sale", async () => {
    calls.sale.mockResolvedValue(sale({ prepared_cart_items: [], items: [legacyItem] }));
    mount();
    const button = await screen.findByRole("button", {
      name: "Выбрать упаковку, блистер или штуки",
    });
    await waitFor(() => expect(button).toBeEnabled());
    fireEvent.click(button);
    await screen.findByRole("region", { name: "Физическая корзина" });
    expect(screen.getByRole("button", { name: "Обновить физическую корзину" })).toBeEnabled();
    expect(calls.create).not.toHaveBeenCalled();
  });
  it("checks Rx and persists the global prescription before starting mixed cash", async () => {
    calls.sale.mockResolvedValue(sale({ items: [legacyItem] }));
    calls.catalog.mockImplementation(async (id: string) => ({
      id,
      tenant_id: POS_SCOPE.tenantId,
      is_active: true,
      dispensing_type: id === physicalItem.catalog_id ? "prescription" : "otc",
    }));
    calls.prescription.mockResolvedValue({ id: "rx-1" });
    mount();
    await cashPanelCallbacks();
    let panel = calls.panel.mock.calls.at(-1)?.[0] as { beforeStart: () => Promise<void> };
    await act(async () => {
      await expect(panel.beforeStart()).rejects.toThrow("Сначала заполните рецепт");
    });
    expect(calls.prescription).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByRole("button", { name: "Сохранить тестовый рецепт" }));
    await act(async () => {
      panel = calls.panel.mock.calls.at(-1)?.[0] as { beforeStart: () => Promise<void> };
      await panel.beforeStart();
    });
    expect(calls.prescription).toHaveBeenCalledWith("sale-1", { prescription_number: "RX-test" });
  });

  it("opens an empty restored draft editor and returns without creating another sale", async () => {
    calls.sale.mockResolvedValue(sale({ prepared_cart_items: [] }));
    mount();
    const open = await screen.findByRole("button", { name: "Выбрать упаковку, блистер или штуки" });
    await waitFor(() => expect(open).toBeEnabled());
    fireEvent.click(open);
    await screen.findByRole("region", { name: "Физическая корзина" });
    fireEvent.click(screen.getByRole("button", { name: "Вернуться к обычной продаже" }));
    await waitFor(() =>
      expect(screen.queryByRole("region", { name: "Физическая корзина" })).not.toBeInTheDocument(),
    );
    expect(calls.create).not.toHaveBeenCalled();
  });

  it("does not mount the draft editor for a completed receipt", async () => {
    calls.sale.mockResolvedValue(
      sale({
        status: "completed",
        receipt_number: "receipt-1",
        completed_at: "2026-10-08T08:06:00Z",
      }),
    );
    const { client } = mount();
    await waitFor(() =>
      expect(client.getQueryData(posKeys.sale("sale-1"))).toMatchObject({ status: "completed" }),
    );
    expect(screen.queryByRole("region", { name: "Физическая корзина" })).not.toBeInTheDocument();
    expect(calls.editor).not.toHaveBeenCalled();
  });

  it.each(["logout", "user", "tenant"])(
    "does not refetch through the old editor callback after %s",
    async (change) => {
      mount();
      await screen.findByRole("region", { name: "Физическая корзина" });
      const props = calls.editor.mock.calls.at(-1)?.[0] as {
        sessionIsActive: () => boolean;
        onChanged: () => Promise<void>;
        onRecoveryChange: (operation: PhysicalCartRecovery | null) => void;
      };
      const operation: PhysicalCartRecovery = {
        kind: "delete",
        itemId: "20000000-0000-4000-8000-000000000001",
      };
      props.onRecoveryChange(operation);
      expect(loadPhysicalCartRecovery("sale-1", POS_SCOPE)).toEqual(operation);
      const before = calls.sale.mock.calls.length;
      act(() => {
        if (change === "logout") useAuthStore.getState().clear();
        else
          authenticatePos({
            tenantId: change === "tenant" ? "t-2" : "t-1",
            userId: change === "user" ? "u-2" : "u-1",
          });
      });
      expect(props.sessionIsActive()).toBe(false);
      expect(() => props.onRecoveryChange(null)).toThrow("Сессия кассира завершена");
      expect(loadPhysicalCartRecovery("sale-1", POS_SCOPE)).toEqual(operation);
      await act(async () => props.onChanged());
      expect(calls.sale).toHaveBeenCalledTimes(before);
      expect(calls.addPayment).not.toHaveBeenCalled();
      expect(calls.checkout).not.toHaveBeenCalled();
    },
  );

  it("keeps editor authority and refetch valid across token rotation", async () => {
    useAuthStore.setState({ accessToken: "synthetic-before" });
    mount();
    await screen.findByRole("region", { name: "Физическая корзина" });
    const props = calls.editor.mock.calls.at(-1)?.[0] as {
      sessionIsActive: () => boolean;
      onChanged: () => Promise<void>;
    };
    const before = calls.sale.mock.calls.length;
    act(() => useAuthStore.setState({ accessToken: "synthetic-after" }));
    expect(props.sessionIsActive()).toBe(true);
    await act(async () => props.onChanged());
    expect(calls.sale).toHaveBeenCalledTimes(before + 1);
  });
});
