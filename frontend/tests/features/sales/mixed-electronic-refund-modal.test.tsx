import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { type SaleDetails } from "@/features/pos/types";
import { MixedElectronicRefundModal } from "@/features/sales/MixedElectronicRefundModal";

vi.mock("@tanstack/react-query", () => ({
  useQueryClient: () => ({ invalidateQueries: vi.fn() }),
}));

const mocks = vi.hoisted(() => {
  const permission_scopes: Record<string, string[] | null> = {
    "pos.refund": null,
    "pos.refund_external_confirm": null,
  };
  return {
    options: vi.fn(),
    quote: vi.fn(),
    inspect: vi.fn(),
    load: vi.fn(),
    save: vi.fn(),
    command: vi.fn(),
    ack: vi.fn(),
    shift: vi.fn(),
    active: true,
    user: {
      id: "00000000-0000-4000-8000-000000000002",
      active_tenant_id: "00000000-0000-4000-8000-000000000001",
      is_developer: false,
      permissions: ["pos.refund", "pos.refund_external_confirm"],
      permission_scopes,
      support_access: null as { is_read_only: boolean } | null,
    },
  };
});
vi.mock("@/features/auth/hooks", () => ({ useAuth: () => ({ user: mocks.user }) }));
vi.mock("@/stores/auth", () => ({ useAuthStore: { getState: () => ({ user: mocks.user }) } }));
vi.mock("@/features/pos/posStorageScope", () => ({
  currentPosStorageScope: () => ({ userId: mocks.user.id, tenantId: mocks.user.active_tenant_id }),
  capturePosSessionGuard: () => () => mocks.active,
}));
vi.mock("@/features/pos/queries", () => ({ useCurrentShiftQuery: mocks.shift }));
vi.mock("@/features/sales/mixedElectronicRefundOptions", () => ({
  getMixedElectronicRefundOptions: mocks.options,
}));
vi.mock("@/features/sales/mixedElectronicRefundReview", async (original) => ({
  ...(await original<typeof import("@/features/sales/mixedElectronicRefundReview")>()),
  quoteMixedElectronicRefundReview: mocks.quote,
}));
vi.mock("@/features/sales/mixedElectronicRefundRecovery", () => ({
  loadMixedElectronicRefund: mocks.load,
  inspectMixedElectronicRefund: mocks.inspect,
  saveMixedElectronicRefund: mocks.save,
  commandMixedElectronicRefund: mocks.command,
  acknowledgeMixedElectronicRefund: mocks.ack,
  mixedElectronicRefundKey: () => "test-electronic-journal",
  mixedElectronicRefundReviewScope: ({
    tenantId,
    userId,
    parentSaleId,
    branchId,
  }: {
    tenantId: string;
    userId: string;
    parentSaleId: string;
    branchId: string;
  }) => ({ tenantId, userId, parentSaleId, branchId }),
}));
const id = (n: number) => `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
const sale: SaleDetails = {
  id: id(3),
  tenant_id: id(1),
  branch_id: id(4),
  register_id: id(5),
  shift_id: id(6),
  sale_type: "sale",
  parent_sale_id: null,
  status: "completed",
  receipt_number: "S-1",
  is_test: false,
  total_amount: "4.00",
  currency: "TJS",
  voided_at: null,
  voided_by_sale_id: null,
  cashier_user_id: id(2),
  created_at: "2026-10-20T10:00:00Z",
  completed_at: "2026-10-20T10:00:00Z",
  items: [],
  physical_items: [],
  mixed_items: [
    {
      position: 1,
      name: "Лекарство",
      qty: "4",
      unit_price: "1.00",
      discount_amount: "0.00",
      total_price: "4.00",
    },
  ],
  payments: [
    {
      id: id(9),
      sale_id: id(3),
      operation_id: id(10),
      payment_method: "card",
      amount: "4.00",
      currency: "TJS",
    },
  ],
};
const options = () => ({
  parent_sale_id: sale.id,
  branch_id: sale.branch_id,
  register_id: sale.register_id,
  original_receipt_number: "S-1",
  reason_mode: "optional",
  lines: [
    {
      inventory_kind: "physical_unit",
      line_id: id(7),
      name: "Лекарство",
      unit_label: "табл.",
      packaging: {
        version: 2,
        unit: "tablet",
        units_per_pack: 20,
        units_per_blister: 10,
        dispense_step: 2,
      },
      sold_base_units: 20,
      returned_base_units: 0,
      remaining_base_units: 20,
      original_sale_format: "pack",
      original_retail_unit_price: "4.00",
    },
  ],
});
const preview = () => ({
  state: "preview",
  parent_sale_id: sale.id,
  branch_id: sale.branch_id,
  original_receipt_number: "S-1",
  revision_hash: "a".repeat(64),
  total_amount: "2.00",
  currency: "TJS",
  lines: [
    {
      inventory_kind: "physical_unit",
      line_id: id(7),
      sale_format: "unit",
      quantity: 2,
      base_units: 2,
      offset: 0,
      gross_amount: "2.00",
      discount_amount: "0.00",
      net_amount: "2.00",
    },
  ],
  payments: [{ original_payment_id: id(9), method: "card", amount: "2.00", currency: "TJS" }],
});
const recovery = () => ({
  pending: { version: 1, attempted: "save", request: { operation_id: id(8) } },
  review: { preview: preview() },
});
const open = () =>
  render(<MixedElectronicRefundModal sale={sale} onClose={vi.fn()} onRefunded={vi.fn()} />);
beforeEach(() => {
  vi.clearAllMocks();
  mocks.active = true;
  mocks.user.permission_scopes = { "pos.refund": null, "pos.refund_external_confirm": null };
  mocks.user.permissions = ["pos.refund", "pos.refund_external_confirm"];
  mocks.user.support_access = null;
  mocks.shift.mockReturnValue({
    data: {
      id: id(6),
      status: "open",
      opened_by_user_id: id(2),
      branch_id: id(4),
      register_id: id(5),
      currency: "TJS",
    },
  });
  mocks.load.mockReturnValue(null);
  mocks.inspect.mockResolvedValue(null);
  mocks.options.mockResolvedValue(options());
  mocks.quote.mockResolvedValue(preview());
  mocks.save.mockResolvedValue(recovery());
});
afterEach(cleanup);
describe("cashier electronic refund form", () => {
  it("opens with GET options and sends exact selection to server quote before explicit save", async () => {
    open();
    await screen.findByLabelText("Количество: Лекарство");
    expect(mocks.quote).not.toHaveBeenCalled();
    expect(mocks.save).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "Рассчитать электронный возврат" }));
    await screen.findByText("Расчёт сервера: 2.00 TJS");
    expect(mocks.quote).toHaveBeenCalledWith(
      { tenantId: id(1), userId: id(2), parentSaleId: id(3), branchId: id(4) },
      {
        items: [
          { inventory_kind: "physical_unit", line_id: id(7), sale_format: "unit", quantity: 2 },
        ],
      },
    );
    expect(mocks.save).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Сохранить выбранный расчёт" }));
    await screen.findByText("Возврат: 2.00 TJS");
    expect(mocks.save).toHaveBeenCalledTimes(1);
  });
  it.each(["1", "1.5", "22"])("rejects non-whole/non-step/excess quantity %s", async (quantity) => {
    open();
    await screen.findByLabelText("Количество: Лекарство");
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.change(screen.getByLabelText("Количество: Лекарство"), {
      target: { value: quantity },
    });
    fireEvent.submit(
      screen.getByRole("button", { name: "Рассчитать электронный возврат" }).closest("form")!,
    );
    await screen.findByRole("alert");
    expect(mocks.quote).not.toHaveBeenCalled();
  });
  it("invalidates quote after editing selection", async () => {
    open();
    await screen.findByLabelText("Количество: Лекарство");
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "Рассчитать электронный возврат" }));
    await screen.findByText("Расчёт сервера: 2.00 TJS");
    fireEvent.change(screen.getByLabelText("Количество: Лекарство"), { target: { value: "4" } });
    expect(screen.queryByText("Сохранить выбранный расчёт")).not.toBeInTheDocument();
  });
  it.each(["closed", "readonly", "permission"])(
    "blocks new commands for %s but reads pending",
    async (kind) => {
      if (kind === "closed") mocks.shift.mockReturnValue({ data: null });
      if (kind === "readonly") mocks.user.support_access = { is_read_only: true };
      if (kind === "permission") mocks.user.permissions = [];
      mocks.load.mockReturnValue({ request: { operation_id: id(8) } });
      mocks.inspect.mockResolvedValue(recovery());
      open();
      if (kind !== "permission") {
        await screen.findByText("Возврат: 2.00 TJS");
        expect(screen.getByRole("button", { name: "Закрепить расчёт" })).toBeDisabled();
        expect(mocks.inspect).toHaveBeenCalledTimes(1);
      }
      expect(mocks.command).not.toHaveBeenCalled();
    },
  );
  it("preserves journal failure as barrier without quote/save", async () => {
    mocks.load.mockImplementationOnce(() => {
      throw new Error("Журнал повреждён");
    });
    open();
    await screen.findByRole("alert");
    expect(mocks.options).not.toHaveBeenCalled();
    expect(mocks.save).not.toHaveBeenCalled();
  });
  it("discards a quote arriving after unmount", async () => {
    let resolve: (value: ReturnType<typeof preview>) => void = () => undefined;
    mocks.quote.mockReturnValue(
      new Promise((r) => {
        resolve = r;
      }),
    );
    const view = open();
    await screen.findByLabelText("Количество: Лекарство");
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "Рассчитать электронный возврат" }));
    await waitFor(() => expect(mocks.quote).toHaveBeenCalled());
    view.unmount();
    resolve(preview());
    expect(mocks.save).not.toHaveBeenCalled();
  });
});

const activated = (full = false) => ({
  ...recovery(),
  pending: { ...recovery().pending, attempted: "confirm" },
  activation: { state: "activation_saved" },
  evidence: {
    state: full ? "confirmed_evidence_only" : "requires_reconciliation",
    missing_payment_ids: full ? [] : [id(9)],
  },
});
describe("evidence and posting UI", () => {
  it("requires every electronic part before post and validates manual evidence", async () => {
    mocks.load.mockReturnValue({});
    mocks.inspect.mockResolvedValue(activated());
    open();
    await screen.findByLabelText("Терминал 1");
    expect(screen.queryByRole("button", { name: "Провести возврат" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Сохранить ручные реквизиты" }));
    await screen.findByRole("alert");
    expect(mocks.command).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("Терминал 1"), { target: { value: " TEST " } });
    fireEvent.change(screen.getByLabelText("Документ 1"), { target: { value: " DOC " } });
    mocks.command.mockResolvedValue(activated(true));
    fireEvent.click(screen.getByRole("button", { name: "Сохранить ручные реквизиты" }));
    await screen.findByRole("button", { name: "Провести возврат" });
    expect(mocks.command.mock.calls[0]?.[2]).toEqual({
      kind: "confirm",
      confirmation: { original_payment_id: id(9), terminal_id: "TEST", document_number: "DOC" },
    });
    expect(screen.getByText(/Это не ответ банка/)).toBeInTheDocument();
  });
  it("reads without external-confirm permission but blocks evidence recording", async () => {
    mocks.user.permissions = ["pos.refund"];
    mocks.load.mockReturnValue({});
    mocks.inspect.mockResolvedValue(activated());
    open();
    expect(await screen.findByLabelText("Терминал 1")).toBeDisabled();
    expect(mocks.inspect).toHaveBeenCalledTimes(1);
    expect(mocks.command).not.toHaveBeenCalled();
  });
  it("requires reason and comment then posts selected shift and exact fields", async () => {
    mocks.options.mockResolvedValue({ ...options(), reason_mode: "required_with_text" });
    mocks.load.mockReturnValue({});
    mocks.inspect.mockResolvedValue(activated(true));
    mocks.command.mockResolvedValue(activated(true));
    open();
    const button = await screen.findByRole("button", { name: "Провести возврат" });
    await waitFor(() => expect(button).toBeEnabled());
    fireEvent.click(button);
    await screen.findByText("Выберите причину возврата");
    expect(mocks.command).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("Причина"), { target: { value: "other" } });
    fireEvent.click(button);
    await screen.findByText("Поясните причину возврата");
    fireEvent.change(screen.getByLabelText("Пояснение"), { target: { value: "Причина" } });
    fireEvent.click(button);
    await waitFor(() => expect(mocks.command).toHaveBeenCalledTimes(1));
    expect(mocks.command.mock.calls[0]?.[2]).toEqual({
      kind: "post",
      details: { shift_id: id(6), reason_code: "other", comment: "Причина" },
    });
  });
  it("acknowledges verified posting with closed shift and opens saved receipt", async () => {
    mocks.shift.mockReturnValue({ data: null });
    mocks.load.mockReturnValue({});
    mocks.inspect.mockResolvedValue({ ...activated(true), posted: { sale_id: id(12) } });
    mocks.ack.mockResolvedValue(id(12));
    const done = vi.fn();
    render(<MixedElectronicRefundModal sale={sale} onClose={vi.fn()} onRefunded={done} />);
    fireEvent.click(
      await screen.findByRole("button", { name: "Открыть сохранённый чек возврата" }),
    );
    await waitFor(() => expect(done).toHaveBeenCalledWith(id(12)));
    expect(mocks.command).not.toHaveBeenCalled();
    expect(mocks.options).not.toHaveBeenCalled();
  });
});

describe("revoked live authority", () => {
  it("suppresses saved UUID, payment parts and typed evidence on same-session branch loss", async () => {
    const stored = { request: { operation_id: id(8) } };
    mocks.load.mockReturnValue(stored);
    mocks.inspect.mockResolvedValue(activated());
    const view = open();
    await screen.findByText("Возврат: 2.00 TJS");
    fireEvent.change(screen.getByLabelText("Терминал 1"), { target: { value: "MANUAL-EVIDENCE" } });
    mocks.user.permission_scopes["pos.refund"] = [id(99)];
    view.rerender(
      <MixedElectronicRefundModal sale={sale} onClose={vi.fn()} onRefunded={vi.fn()} />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Доступ изменился");
    expect(screen.queryByText("Возврат: 2.00 TJS")).not.toBeInTheDocument();
    expect(screen.queryByText("Карта: 2.00 TJS")).not.toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.queryByDisplayValue("MANUAL-EVIDENCE")).not.toBeInTheDocument();
    expect(mocks.load()).toBe(stored);
    expect(mocks.command).not.toHaveBeenCalled();
    expect(mocks.ack).not.toHaveBeenCalled();
  });
  it.each(["branch", "session"])(
    "hides loaded product options after %s authority loss",
    async (kind) => {
      const view = open();
      await screen.findByLabelText("Количество: Лекарство");
      if (kind === "branch") mocks.user.permission_scopes["pos.refund"] = [id(99)];
      else mocks.active = false;
      view.rerender(
        <MixedElectronicRefundModal sale={sale} onClose={vi.fn()} onRefunded={vi.fn()} />,
      );
      expect(screen.getByRole("alert")).toHaveTextContent("Доступ изменился");
      expect(screen.queryByLabelText("Количество: Лекарство")).not.toBeInTheDocument();
      expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
      expect(mocks.save).not.toHaveBeenCalled();
      expect(mocks.command).not.toHaveBeenCalled();
    },
  );
});

const legacyOption = () => ({
  inventory_kind: "legacy_pack",
  line_id: id(20),
  name: "Старая упаковка",
  unit_label: "уп.",
  original_retail_unit_price: "2.00",
  sold_quantity: "2.000",
  returned_quantity: "0.500",
  remaining_quantity: "1.500",
});
describe("mixed selection and recovery barriers", () => {
  it("saves the original tagged selection with exact legacy thousandths", async () => {
    mocks.options.mockResolvedValue({ ...options(), lines: [...options().lines, legacyOption()] });
    open();
    await screen.findByLabelText("Количество: Старая упаковка");
    screen.getAllByRole("checkbox").forEach((checkbox) => fireEvent.click(checkbox));
    fireEvent.change(screen.getByLabelText("Количество: Старая упаковка"), {
      target: { value: "1.25" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Рассчитать электронный возврат" }));
    await screen.findByText("Расчёт сервера: 2.00 TJS");
    const selection = {
      items: [
        { inventory_kind: "physical_unit", line_id: id(7), sale_format: "unit", quantity: 2 },
        { inventory_kind: "legacy_pack", line_id: id(20), quantity: "1.250" },
      ],
    };
    expect(mocks.quote.mock.calls[0]?.[1]).toEqual(selection);
    fireEvent.click(screen.getByRole("button", { name: "Сохранить выбранный расчёт" }));
    await screen.findByText("Возврат: 2.00 TJS");
    expect(mocks.save.mock.calls[0]?.[1]).toEqual(selection);
    expect(mocks.save.mock.calls[0]?.[2]).toEqual(preview());
  });
  it.each(["0", "1.501", "1.0001", "1e0"])(
    "rejects invalid legacy amount %s without quoting",
    async (quantity) => {
      mocks.options.mockResolvedValue({ ...options(), lines: [legacyOption()] });
      open();
      await screen.findByLabelText("Количество: Старая упаковка");
      fireEvent.click(screen.getByRole("checkbox"));
      fireEvent.change(screen.getByLabelText("Количество: Старая упаковка"), {
        target: { value: quantity },
      });
      fireEvent.click(screen.getByRole("button", { name: "Рассчитать электронный возврат" }));
      await screen.findByRole("alert");
      expect(mocks.quote).not.toHaveBeenCalled();
    },
  );
  it("uses historical blister sizes and rejects unavailable blisters", async () => {
    open();
    await screen.findByLabelText("Формат: Лекарство");
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.change(screen.getByLabelText("Формат: Лекарство"), { target: { value: "blister" } });
    fireEvent.change(screen.getByLabelText("Количество: Лекарство"), { target: { value: "2" } });
    fireEvent.click(screen.getByRole("button", { name: "Рассчитать электронный возврат" }));
    await screen.findByText("Расчёт сервера: 2.00 TJS");
    expect(mocks.quote.mock.calls[0]?.[1]).toEqual({
      items: [
        { inventory_kind: "physical_unit", line_id: id(7), sale_format: "blister", quantity: 2 },
      ],
    });
  });
  it("recovers a lost save after reopening without a second save", async () => {
    const pending = { request: { operation_id: id(8) } };
    mocks.save.mockImplementationOnce(async () => {
      mocks.load.mockReturnValue(pending);
      throw new Error("Ответ потерян");
    });
    const view = open();
    await screen.findByLabelText("Количество: Лекарство");
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "Рассчитать электронный возврат" }));
    fireEvent.click(await screen.findByRole("button", { name: "Сохранить выбранный расчёт" }));
    await screen.findByRole("alert");
    expect(mocks.save).toHaveBeenCalledTimes(1);
    view.unmount();
    mocks.inspect.mockResolvedValue(recovery());
    open();
    await screen.findByText("Возврат: 2.00 TJS");
    expect(mocks.inspect).toHaveBeenCalledTimes(1);
    expect(mocks.save).toHaveBeenCalledTimes(1);
    expect(mocks.load()).toBe(pending);
  });
  it("keeps lost posting fail-closed and recovers by inspection only", async () => {
    mocks.load.mockReturnValue({ request: { operation_id: id(8) } });
    mocks.inspect.mockResolvedValue(activated(true));
    mocks.command.mockRejectedValueOnce(new Error("Ответ потерян"));
    open();
    const post = await screen.findByRole("button", { name: "Провести возврат" });
    await waitFor(() => expect(post).toBeEnabled());
    fireEvent.click(post);
    await screen.findByRole("alert");
    expect(screen.queryByRole("button", { name: "Провести возврат" })).not.toBeInTheDocument();
    mocks.inspect.mockResolvedValue({ ...activated(true), posted: { sale_id: id(12) } });
    fireEvent.click(screen.getByRole("button", { name: "Проверить сохранённый результат" }));
    await screen.findByRole("button", { name: "Открыть сохранённый чек возврата" });
    expect(mocks.command).toHaveBeenCalledTimes(1);
    expect(mocks.save).not.toHaveBeenCalled();
  });
  it("fails closed when options cannot be loaded", async () => {
    mocks.options.mockRejectedValueOnce(new Error("Нет связи"));
    open();
    await screen.findByRole("alert");
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    expect(mocks.quote).not.toHaveBeenCalled();
    expect(mocks.save).not.toHaveBeenCalled();
  });
  it("refreshes journal after another tab changes it without posting", async () => {
    open();
    await screen.findByLabelText("Количество: Лекарство");
    mocks.load.mockReturnValue({ request: { operation_id: id(8) } });
    mocks.inspect.mockResolvedValue(recovery());
    fireEvent(window, new StorageEvent("storage", { key: "test-electronic-journal" }));
    await screen.findByText("Возврат: 2.00 TJS");
    expect(mocks.inspect).toHaveBeenCalledTimes(1);
    expect(mocks.save).not.toHaveBeenCalled();
    expect(mocks.command).not.toHaveBeenCalled();
  });
  it("hides loaded selection when sale context changes", async () => {
    const view = open();
    await screen.findByLabelText("Количество: Лекарство");
    view.rerender(
      <MixedElectronicRefundModal
        sale={{ ...sale, id: id(30) }}
        onClose={vi.fn()}
        onRefunded={vi.fn()}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Доступ изменился");
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    expect(mocks.save).not.toHaveBeenCalled();
  });
});

describe("explicit cashier commands", () => {
  it("binds and activates only after separate cashier decisions", async () => {
    mocks.load.mockReturnValue({});
    mocks.inspect.mockResolvedValue(recovery());
    const bound = {
      ...recovery(),
      binding: { state: "bound" },
      pending: { ...recovery().pending, attempted: "bind" },
    };
    mocks.command
      .mockResolvedValueOnce(bound)
      .mockResolvedValueOnce({ ...activated(), binding: { state: "bound" } });
    open();
    fireEvent.click(await screen.findByRole("button", { name: "Закрепить расчёт" }));
    await waitFor(() => expect(mocks.command).toHaveBeenCalledTimes(1));
    expect(mocks.command.mock.calls[0]?.[2]).toEqual({ kind: "bind" });
    fireEvent.click(await screen.findByRole("button", { name: "Активировать в своей смене" }));
    await screen.findByLabelText("Терминал 1");
    expect(mocks.command.mock.calls[1]?.[2]).toEqual({ kind: "activate", shiftId: id(6) });
    expect(screen.queryByRole("button", { name: "Провести возврат" })).not.toBeInTheDocument();
  });
  it("cancels preparation then acknowledges the verified cancellation", async () => {
    mocks.load.mockReturnValue({});
    mocks.inspect.mockResolvedValue(recovery());
    mocks.command.mockResolvedValueOnce({ ...recovery(), binding: { state: "cancelled" } });
    mocks.ack.mockResolvedValueOnce(sale.id);
    const done = vi.fn();
    render(<MixedElectronicRefundModal sale={sale} onClose={vi.fn()} onRefunded={done} />);
    fireEvent.click(await screen.findByRole("button", { name: "Отменить подготовку" }));
    fireEvent.click(await screen.findByRole("button", { name: "Подтвердить отмену" }));
    await waitFor(() => expect(done).toHaveBeenCalledWith(sale.id));
    expect(mocks.command.mock.calls[0]?.[2]).toEqual({ kind: "cancel" });
    expect(mocks.save).not.toHaveBeenCalled();
  });
  it.each(["loading", "error"])("fails closed for stale shift authority: %s", async (kind) => {
    const prior = mocks.shift();
    mocks.shift.mockReturnValue({
      ...prior,
      isFetching: kind === "loading",
      error: kind === "error" ? new Error("Нет связи") : null,
    });
    open();
    await screen.findByLabelText("Количество: Лекарство");
    expect(screen.getByRole("button", { name: "Рассчитать электронный возврат" })).toBeDisabled();
    expect(mocks.quote).not.toHaveBeenCalled();
  });
});
