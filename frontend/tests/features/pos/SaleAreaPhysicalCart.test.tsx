import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SaleArea } from "@/features/pos/SaleArea";
import { draftKey } from "@/features/pos/draftStorage";
import { type PhysicalCartItem } from "@/features/pos/physicalCart";
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
  addPrescription: vi.fn(),
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
vi.mock("@/features/catalog/api", () => ({ findByBarcode: calls.barcode }));
vi.mock("@/lib/desktopBridge", () => ({
  DESKTOP_BARCODE_SCANNED_EVENT: "aurum-desktop-barcode-scanned",
  normalizeDesktopBarcode: (raw: string) => raw.trim() || null,
  requestDesktopCashDrawerOpen: vi.fn(),
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
  afterEach(cleanup);

  it.each(["keyboard", "touch"] as const)(
    "restores physical rows and blocks all payment entry points in %s mode",
    async (mode) => {
      mount(mode);
      await screen.findByRole("region", { name: "Физическая корзина" });
      expect(screen.getByText("Оплата пока недоступна")).toBeInTheDocument();
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

  it("keeps a mixed legacy/physical draft read-only instead of enabling old payment", async () => {
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
      screen.getByText("В чеке есть обычные и поштучные позиции. Оплата пока недоступна."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Обновить физическую корзину" })).toBeDisabled();
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
