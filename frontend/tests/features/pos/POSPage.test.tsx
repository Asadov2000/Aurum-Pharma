import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const getCurrentShift = vi.fn();
const openShift = vi.fn();
const closeShift = vi.fn();
const getZReportXlsx = vi.fn();
const listRegisters = vi.fn();
const getPosFavorites = vi.fn();
const getSale = vi.fn();

vi.mock("@/features/pos/api", () => ({
  getCurrentShift: (...a: unknown[]) => getCurrentShift(...a),
  openShift: (...a: unknown[]) => openShift(...a),
  closeShift: (...a: unknown[]) => closeShift(...a),
  getZReportXlsx: (...a: unknown[]) => getZReportXlsx(...a),
  getZReport: vi.fn(),
  createSale: vi.fn(),
  getSale: (...a: unknown[]) => getSale(...a),
  addSaleItem: vi.fn(),
  updateSaleItem: vi.fn(),
  deleteSaleItem: vi.fn(),
  addPayment: vi.fn(),
  completeSale: vi.fn(),
  addPrescription: vi.fn(),
  getPosFavorites: (...args: unknown[]) => getPosFavorites(...args),
  addPosFavorite: vi.fn(),
  removePosFavorite: vi.fn(),
}));

vi.mock("@/features/foundation/api", () => ({
  listRegisters: (...a: unknown[]) => listRegisters(...a),
  listBranches: vi.fn().mockResolvedValue([]),
  listTenants: vi.fn(),
  createTenant: vi.fn(),
  updateTenant: vi.fn(),
  getTenantOperationalSettings: vi.fn().mockResolvedValue({
    draft_sale_lifetime_min: 60,
    pos_payment_methods: ["cash", "card", "qr"],
    pos_mixed_payment_enabled: true,
  }),
  updateTenantSettings: vi.fn(),
  createBranch: vi.fn(),
  updateBranch: vi.fn(),
  deleteBranch: vi.fn(),
  createRegister: vi.fn(),
  updateRegister: vi.fn(),
  deleteRegister: vi.fn(),
}));

vi.mock("@/features/catalog/pickerQueries", () => ({
  useCatalogPickerQuery: () => ({ data: undefined }),
}));

vi.mock("@/features/pos/usePosRegisterLock", () => ({
  usePosRegisterLock: () => ({ status: "owned", isOwner: true, message: null }),
}));

import { POSPage } from "@/features/pos/POSPage";
import { draftKey } from "@/features/pos/draftStorage";
import {
  defaultDevicePreferences,
  devicePreferencesScope,
  loadDevicePreferences,
  saveDevicePreferences,
} from "@/lib/devicePreferences";
import { useAuthStore } from "@/stores/auth";

async function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  let view: ReturnType<typeof render> | undefined;
  await act(async () => {
    view = render(
      <QueryClientProvider client={qc}>
        <POSPage />
      </QueryClientProvider>,
    );
    await Promise.resolve();
  });
  if (!view) throw new Error("POS page did not render");
  return view;
}

const REGISTER = {
  id: "r-1",
  tenant_id: "t-1",
  branch_id: "b-1",
  name: "Касса 1",
  printer_type: null,
  printer_config: null,
  card_terminal_id: "TERM-POS-01",
  qr_terminal_id: "QR-POS-01",
  is_active: true,
  created_at: "2026-05-23T00:00:00Z",
  updated_at: "2026-05-23T00:00:00Z",
};

const OPEN_SHIFT = {
  id: "sh-1",
  tenant_id: "t-1",
  branch_id: "b-1",
  register_id: REGISTER.id,
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

const POS_USER = {
  id: "u-1",
  email: "cashier@aurum.tj",
  full_name: "Кассир",
  is_developer: false,
  is_administrator: false,
  home_tenant_id: "t-1",
  active_tenant_id: "t-1",
  status: "active",
  last_login_at: null,
  level: 1,
  is_tenant_owner: false,
  branch_assignments: {},
  permissions: ["pos.shift_open", "pos.shift_close", "pos.sell"],
  support_access: null,
};

describe("POSPage", () => {
  beforeEach(() => {
    setOnline(true);
    window.localStorage.clear();
    useAuthStore.getState().setUser(POS_USER);
    getCurrentShift.mockReset();
    openShift.mockReset();
    closeShift.mockReset();
    getZReportXlsx.mockReset();
    listRegisters.mockReset();
    getPosFavorites.mockReset();
    getPosFavorites.mockResolvedValue([]);
    getSale.mockReset();
    getZReportXlsx.mockRejectedValue(new Error("download unavailable"));
  });
  afterEach(() => {
    setOnline(true);
    vi.clearAllMocks();
    act(() => useAuthStore.getState().clear());
  });

  it("tells a cashier who can provide a working register", async () => {
    listRegisters.mockResolvedValueOnce([]);
    await renderPage();
    expect(await screen.findByText("Нет доступной рабочей кассы")).toBeInTheDocument();
    expect(
      screen.getByText(/Обратитесь к владельцу или ответственному сотруднику/i),
    ).toBeInTheDocument();
  });

  it("directs an authorized user to create a working register", async () => {
    useAuthStore.getState().setUser({
      ...POS_USER,
      permissions: [...POS_USER.permissions, "registers.create"],
    });
    listRegisters.mockResolvedValueOnce([]);
    await renderPage();

    expect(
      await screen.findByText(/Добавьте рабочую кассу в разделе «Рабочие кассы»/i),
    ).toBeInTheDocument();
  });

  it("retries loading working registers without reloading the page", async () => {
    listRegisters.mockRejectedValueOnce(new Error("network"));
    listRegisters.mockResolvedValueOnce([]);
    await renderPage();

    expect(await screen.findByText(/Продажа пока недоступна/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Повторить" }));

    expect(await screen.findByText("Нет доступной рабочей кассы")).toBeInTheDocument();
    expect(listRegisters).toHaveBeenCalledTimes(2);
  });

  it("auto-selects the only register (no manual choice) and shows the open-shift form", async () => {
    listRegisters.mockResolvedValue([REGISTER]);
    getCurrentShift.mockResolvedValue(null);
    await renderPage();
    expect(await screen.findByLabelText(/Наличные в кассе на начало смены/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Открыть смену/i })).toBeInTheDocument();
    // No dropdown to choose from — the single register is shown in a disabled field.
    expect(screen.queryByText("— выберите —")).not.toBeInTheDocument();
    expect(screen.getByDisplayValue(REGISTER.name)).toBeInTheDocument();
    expect(
      loadDevicePreferences(devicePreferencesScope(POS_USER.id, POS_USER.active_tenant_id))
        .lastRegisterId,
    ).toBe(REGISTER.id);
  });

  it("requires a manual choice when two or more registers exist", async () => {
    const second = { ...REGISTER, id: "r-2", name: "Касса 2" };
    listRegisters.mockResolvedValue([REGISTER, second]);
    getCurrentShift.mockResolvedValue(null);
    await renderPage();
    // Wait for the register options to load (placeholder + both registers).
    await screen.findByRole("option", { name: "Касса 2" });
    expect(screen.getByText("— выберите —")).toBeInTheDocument();
    // Nothing is auto-selected, so the open-shift form is not shown yet.
    expect(screen.queryByLabelText(/Наличные в кассе на начало смены/i)).not.toBeInTheDocument();

    // Choosing a register proceeds to the shift form.
    fireEvent.change(screen.getByLabelText("Касса"), { target: { value: second.id } });
    expect(await screen.findByLabelText(/Наличные в кассе на начало смены/i)).toBeInTheDocument();
    expect(
      loadDevicePreferences(devicePreferencesScope(POS_USER.id, POS_USER.active_tenant_id))
        .lastRegisterId,
    ).toBe(second.id);
  });

  it("preserves a non-empty draft before switching to another register", async () => {
    const second = { ...REGISTER, id: "r-2", name: "Касса 2" };
    listRegisters.mockResolvedValue([REGISTER, second]);
    window.localStorage.setItem("pos:lastRegisterId", REGISTER.id);
    window.localStorage.setItem(
      draftKey(REGISTER.id),
      JSON.stringify({ saleId: "sale-1", nameById: {}, savedAt: Date.now() }),
    );
    getCurrentShift.mockImplementation((registerId: string) =>
      Promise.resolve({ ...OPEN_SHIFT, register_id: registerId }),
    );
    getSale.mockResolvedValue({
      id: "sale-1",
      tenant_id: "t-1",
      branch_id: "b-1",
      register_id: REGISTER.id,
      shift_id: OPEN_SHIFT.id,
      sale_type: "sale",
      parent_sale_id: null,
      status: "draft",
      receipt_number: null,
      operation_id: null,
      is_test: false,
      total_amount: "10.00",
      currency: "TJS",
      voided_at: null,
      voided_by_sale_id: null,
      cashier_user_id: POS_USER.id,
      created_at: OPEN_SHIFT.opened_at,
      completed_at: null,
      items: [
        {
          id: "item-1",
          sale_id: "sale-1",
          catalog_id: "catalog-1",
          batch_id: "batch-1",
          qty: "1",
          unit_price: "10.00",
          total_price: "10.00",
          currency: "TJS",
          discount_amount: "0.00",
          position: 1,
        },
      ],
      payments: [],
    });

    await renderPage();
    const registerSelect = await screen.findByLabelText("Касса");
    await screen.findByText("Текущий чек");
    fireEvent.change(registerSelect, { target: { value: second.id } });

    expect(registerSelect).toHaveValue(REGISTER.id);
    expect(screen.getByRole("dialog", { name: "Перейти на другую кассу?" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Сохранить чек и перейти" }));

    await waitFor(() => expect(screen.getByLabelText("Касса")).toHaveValue(second.id));
    expect(window.localStorage.getItem(draftKey(REGISTER.id))).not.toBeNull();
  });

  it("hides shift and sale actions that are outside the account permissions", async () => {
    useAuthStore.getState().setUser({
      ...POS_USER,
      permissions: ["pos.shift_close"],
    });
    listRegisters.mockResolvedValue([REGISTER]);
    getCurrentShift.mockResolvedValue(null);

    await renderPage();

    expect(
      await screen.findByText("Открытие смены недоступно для этого аккаунта."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Открыть смену/i })).not.toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/Поиск товара/i)).not.toBeInTheDocument();
  });

  it("opens the shift with the entered opening cash", async () => {
    listRegisters.mockResolvedValue([REGISTER]);
    getCurrentShift.mockResolvedValue(null);
    openShift.mockResolvedValueOnce(OPEN_SHIFT);
    await renderPage();
    const cashInput = await screen.findByLabelText(/Наличные в кассе на начало смены/i);
    fireEvent.change(cashInput, { target: { value: "250" } });
    fireEvent.click(screen.getByRole("button", { name: /Открыть смену/i }));
    await waitFor(() => {
      expect(openShift).toHaveBeenCalledWith({
        register_id: REGISTER.id,
        opening_cash: "250",
      });
    });
  });

  it.each(["-1", "1e3", "1.234", "Infinity", ""])(
    "rejects an invalid opening cash value: %s",
    async (invalidAmount) => {
      listRegisters.mockResolvedValue([REGISTER]);
      getCurrentShift.mockResolvedValue(null);
      await renderPage();

      const cashInput = await screen.findByLabelText(/Наличные в кассе на начало смены/i);
      fireEvent.change(cashInput, { target: { value: invalidAmount } });
      fireEvent.click(screen.getByRole("button", { name: /Открыть смену/i }));

      expect(await screen.findByRole("alert")).toHaveTextContent(/с точностью до копейки/i);
      expect(openShift).not.toHaveBeenCalled();
      expect(cashInput).toHaveFocus();
    },
  );

  it("uses F9 to focus the opening amount without submitting a zero-cash shift", async () => {
    saveDevicePreferences(devicePreferencesScope(POS_USER.id, POS_USER.active_tenant_id), {
      ...defaultDevicePreferences(),
      posMode: "keyboard",
    });
    listRegisters.mockResolvedValue([REGISTER]);
    getCurrentShift.mockResolvedValue(null);
    await renderPage();

    const cashInput = await screen.findByLabelText(/Наличные в кассе на начало смены/i);
    fireEvent.keyDown(window, { key: "F9" });

    expect(cashInput).toHaveFocus();
    expect(openShift).not.toHaveBeenCalled();
  });

  it("blocks shift mutations while the browser is offline", async () => {
    setOnline(false);
    listRegisters.mockResolvedValue([REGISTER]);
    getCurrentShift.mockResolvedValue(null);
    await renderPage();

    const openButton = await screen.findByRole("button", { name: /Открыть смену/i });
    expect(openButton).toBeDisabled();
    fireEvent.click(openButton);
    expect(openShift).not.toHaveBeenCalled();
  });

  it("locks the active selling workspace while the browser is offline", async () => {
    setOnline(false);
    listRegisters.mockResolvedValue([REGISTER]);
    getCurrentShift.mockResolvedValue(OPEN_SHIFT);
    await renderPage();

    expect(await screen.findByText(/Текущий чек сохранён на этом устройстве/i)).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/Найти товар или отсканировать/i)).toBeDisabled();
    expect(screen.getByRole("button", { name: "Закрыть смену" })).toBeDisabled();
  });

  it("recovers an already-open shift after a conflicting open response", async () => {
    listRegisters.mockResolvedValue([REGISTER]);
    getCurrentShift.mockResolvedValueOnce(null).mockResolvedValue(OPEN_SHIFT);
    openShift.mockRejectedValueOnce(new Error("response lost"));
    await renderPage();

    fireEvent.click(await screen.findByRole("button", { name: /Открыть смену/i }));

    await waitFor(() => {
      expect(screen.getByText(/Смена открыта/i)).toBeInTheDocument();
    });
    expect(screen.queryByText(/Не удалось открыть смену/i)).not.toBeInTheDocument();
  });

  it("remains usable when browser storage is blocked", async () => {
    listRegisters.mockResolvedValue([REGISTER]);
    getCurrentShift.mockResolvedValue(null);
    const getItem = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("storage blocked");
    });
    const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("storage blocked");
    });

    try {
      await renderPage();
      expect(await screen.findByLabelText(/Наличные в кассе на начало смены/i)).toBeInTheDocument();
      expect(screen.getByDisplayValue(REGISTER.name)).toBeInTheDocument();
    } finally {
      getItem.mockRestore();
      setItem.mockRestore();
    }
  });

  it("does not report a successful shift close as failed when browser storage is blocked", async () => {
    listRegisters.mockResolvedValue([REGISTER]);
    getCurrentShift.mockResolvedValue(OPEN_SHIFT);
    closeShift.mockResolvedValue({ ...OPEN_SHIFT, status: "closed" });
    const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("storage blocked");
    });

    try {
      await renderPage();
      fireEvent.click(await screen.findByRole("button", { name: "Закрыть смену" }));
      fireEvent.change(screen.getByLabelText("Наличные после пересчёта"), {
        target: { value: "100" },
      });
      fireEvent.click(screen.getByRole("button", { name: "Подтвердить закрытие смены" }));

      await waitFor(() =>
        expect(closeShift).toHaveBeenCalledWith(OPEN_SHIFT.id, {
          closing_cash_actual: "100",
          notes: null,
        }),
      );
      expect(getZReportXlsx).not.toHaveBeenCalled();
      expect(screen.queryByText(/Не удалось закрыть смену/i)).not.toBeInTheDocument();
    } finally {
      setItem.mockRestore();
    }
  });

  it("downloads the closed-shift report only for an account with export permission", async () => {
    useAuthStore.getState().setUser({
      ...POS_USER,
      permissions: [...POS_USER.permissions, "reports.export"],
    });
    listRegisters.mockResolvedValue([REGISTER]);
    getCurrentShift.mockResolvedValue(OPEN_SHIFT);
    closeShift.mockResolvedValue({ ...OPEN_SHIFT, status: "closed" });
    getZReportXlsx.mockResolvedValue(new Blob(["xlsx"]));

    await renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Закрыть смену" }));
    fireEvent.change(screen.getByLabelText("Наличные после пересчёта"), {
      target: { value: "100" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить закрытие смены" }));

    await waitFor(() => expect(getZReportXlsx).toHaveBeenCalledWith(OPEN_SHIFT.id));
  });

  it("recovers a closed shift when the close response is lost", async () => {
    let closeWasSent = false;
    listRegisters.mockResolvedValue([REGISTER]);
    getCurrentShift.mockImplementation(() => Promise.resolve(closeWasSent ? null : OPEN_SHIFT));
    closeShift.mockImplementationOnce(() => {
      closeWasSent = true;
      return Promise.reject(new Error("response lost"));
    });
    await renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "Закрыть смену" }));
    fireEvent.change(screen.getByLabelText("Наличные после пересчёта"), {
      target: { value: "100,25" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить закрытие смены" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(closeShift).toHaveBeenCalledWith(OPEN_SHIFT.id, {
      closing_cash_actual: "100.25",
      notes: null,
    });
    expect(screen.queryByText(/Не удалось закрыть смену/i)).not.toBeInTheDocument();
    expect(window.localStorage.getItem("pos:lastClosedShiftId")).toBe(OPEN_SHIFT.id);
  });
});

function setOnline(value: boolean): void {
  Object.defineProperty(window.navigator, "onLine", { configurable: true, value });
}
