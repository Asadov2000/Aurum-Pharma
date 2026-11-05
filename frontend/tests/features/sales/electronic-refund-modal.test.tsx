import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { type SaleDetails } from "@/features/pos/types";
import { ElectronicRefundModal } from "@/features/sales/ElectronicRefundModal";
import { isElectronicRefundCandidate } from "@/features/sales/electronicRefundEligibility";

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
vi.mock("@/features/sales/physicalRefund", async (original) => ({
  ...(await original<typeof import("@/features/sales/physicalRefund")>()),
  getPhysicalRefundOptions: mocks.options,
}));
vi.mock("@/features/sales/electronicRefundReview", async (original) => ({
  ...(await original<typeof import("@/features/sales/electronicRefundReview")>()),
  quoteElectronicRefundReview: mocks.quote,
}));
vi.mock("@/features/sales/electronicRefundRecovery", () => ({
  loadElectronicRefund: mocks.load,
  inspectElectronicRefund: mocks.inspect,
  saveElectronicRefund: mocks.save,
  commandElectronicRefund: mocks.command,
  acknowledgeElectronicRefund: mocks.ack,
  electronicRefundKey: () => "test-electronic-journal",
  electronicRefundReviewScope: ({
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
  physical_items: [
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
  render(<ElectronicRefundModal sale={sale} onClose={vi.fn()} onRefunded={vi.fn()} />);
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
describe("electronic candidate routing", () => {
  it.each(["card", "qr"] as const)("accepts real physical %s only as candidate", (method) =>
    expect(
      isElectronicRefundCandidate({
        ...sale,
        payments: [{ ...sale.payments[0]!, payment_method: method }],
      }),
    ).toBe(true),
  );
  it("accepts cash/card split", () =>
    expect(
      isElectronicRefundCandidate({
        ...sale,
        payments: [...sale.payments, { ...sale.payments[0]!, id: id(11), payment_method: "cash" }],
      }),
    ).toBe(true));
  it.each([
    { is_test: true },
    { status: "draft" as const },
    { physical_items: [] },
    { mixed_items: sale.physical_items },
    { payments: [] },
    { payments: [{ ...sale.payments[0]!, amount: "0.00" }] },
  ])("excludes unsupported details %j", (patch) =>
    expect(isElectronicRefundCandidate({ ...sale, ...patch })).toBe(false),
  );
});
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
      { items: [{ line_id: id(7), sale_format: "unit", quantity: 2 }] },
    );
    expect(mocks.save).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Сохранить выбранный расчёт" }));
    await screen.findByText(`Операция: ${id(8)}`);
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
        await screen.findByText(`Операция: ${id(8)}`);
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
    render(<ElectronicRefundModal sale={sale} onClose={vi.fn()} onRefunded={done} />);
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
    await screen.findByText(`Операция: ${id(8)}`);
    fireEvent.change(screen.getByLabelText("Терминал 1"), { target: { value: "MANUAL-EVIDENCE" } });
    mocks.user.permission_scopes["pos.refund"] = [id(99)];
    view.rerender(<ElectronicRefundModal sale={sale} onClose={vi.fn()} onRefunded={vi.fn()} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Доступ изменился");
    expect(screen.queryByText(`Операция: ${id(8)}`)).not.toBeInTheDocument();
    expect(screen.queryByText("card: 2.00 TJS")).not.toBeInTheDocument();
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
      view.rerender(<ElectronicRefundModal sale={sale} onClose={vi.fn()} onRefunded={vi.fn()} />);
      expect(screen.getByRole("alert")).toHaveTextContent("Доступ изменился");
      expect(screen.queryByLabelText("Количество: Лекарство")).not.toBeInTheDocument();
      expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
      expect(mocks.save).not.toHaveBeenCalled();
      expect(mocks.command).not.toHaveBeenCalled();
    },
  );
});
