import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { type SaleDetails } from "@/features/pos/types";
import { MixedRefundModal } from "@/features/sales/MixedRefundModal";

const mocks = vi.hoisted(() => ({
  options: vi.fn(),
  quote: vi.fn(),
  load: vi.fn(),
  inspect: vi.fn(),
  run: vi.fn(),
  runZero: vi.fn(),
  ack: vi.fn(),
  abandon: vi.fn(),
  shift: vi.fn(),
  invalidate: vi.fn(),
  active: true,
}));
vi.mock("@tanstack/react-query", async (original) => ({
  ...(await original<typeof import("@tanstack/react-query")>()),
  useQueryClient: () => ({ invalidateQueries: mocks.invalidate }),
}));
vi.mock("@/features/pos/posStorageScope", () => ({
  currentPosStorageScope: () => ({
    tenantId: "10000000-0000-4000-8000-000000000001",
    userId: "10000000-0000-4000-8000-000000000002",
  }),
  capturePosSessionGuard: () => () => mocks.active,
}));
vi.mock("@/features/pos/queries", () => ({ useCurrentShiftQuery: mocks.shift }));
vi.mock("@/features/sales/mixedRefund", async (original) => ({
  ...(await original<typeof import("@/features/sales/mixedRefund")>()),
  getMixedRefundOptions: mocks.options,
  quoteMixedRefund: mocks.quote,
}));
vi.mock("@/features/sales/mixedRefundRecovery", async (original) => ({
  ...(await original<typeof import("@/features/sales/mixedRefundRecovery")>()),
  loadMixedRefund: mocks.load,
  inspectMixedRefund: mocks.inspect,
  runMixedRefund: mocks.run,
  runMixedZeroReturn: mocks.runZero,
  acknowledgeMixedRefund: mocks.ack,
  abandonMixedRefund: mocks.abandon,
}));
const uuid = (n: number): string => `10000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
const sale: SaleDetails = {
  id: uuid(3),
  tenant_id: uuid(1),
  branch_id: uuid(4),
  register_id: uuid(5),
  shift_id: uuid(6),
  sale_type: "sale",
  parent_sale_id: null,
  status: "completed",
  receipt_number: "M-1",
  is_test: false,
  total_amount: "140.00",
  currency: "TJS",
  voided_at: null,
  voided_by_sale_id: null,
  cashier_user_id: uuid(2),
  created_at: "2026-10-12T08:00:00Z",
  completed_at: "2026-10-12T08:00:00Z",
  items: [],
  payments: [],
};
const options = {
  parent_sale_id: sale.id,
  branch_id: sale.branch_id,
  register_id: sale.register_id,
  original_receipt_number: "M-1",
  reason_mode: "optional",
  lines: [
    {
      inventory_kind: "legacy_pack",
      line_id: uuid(7),
      name: "Дробные пачки",
      unit_label: "уп.",
      sold_quantity: "2.000",
      returned_quantity: "0.000",
      remaining_quantity: "2.000",
      original_retail_unit_price: "10.00",
    },
    {
      inventory_kind: "physical_unit",
      line_id: uuid(8),
      name: "Физические таблетки",
      unit_label: "табл.",
      packaging: {
        version: 2,
        unit: "tablet",
        units_per_pack: 30,
        units_per_blister: 10,
        dispense_step: 1,
      },
      sold_base_units: 60,
      returned_base_units: 0,
      remaining_base_units: 60,
      original_sale_format: "pack",
      original_retail_unit_price: "60.00",
    },
  ],
};
const quote = {
  parent_sale_id: sale.id,
  revision_hash: "a".repeat(64),
  total_amount: "2.01",
  currency: "TJS",
  lines: [
    {
      inventory_kind: "legacy_pack",
      line_id: uuid(7),
      quantity: "0.001",
      offset: "0.000",
      gross_amount: "0.01",
      discount_amount: "0.00",
      net_amount: "0.01",
    },
    {
      inventory_kind: "physical_unit",
      line_id: uuid(8),
      sale_format: "unit",
      quantity: 1,
      base_units: 1,
      offset: 0,
      gross_amount: "2.00",
      discount_amount: "0.00",
      net_amount: "2.00",
    },
  ],
};
const completed = {
  state: "completed",
  operation_id: uuid(9),
  parent_sale_id: sale.id,
  sale_id: uuid(10),
  total_amount: "2.01",
  receipt: { receipt_number: "MR-2" },
};
const pending = {
  version: 1,
  tenantId: uuid(1),
  userId: uuid(2),
  parentSaleId: sale.id,
  branchId: sale.branch_id,
  registerId: sale.register_id,
  definiteRejected: false,
  request: {
    operation_id: uuid(9),
    shift_id: sale.shift_id,
    items: [
      { inventory_kind: "legacy_pack", line_id: uuid(7), quantity: "0.001" },
      { inventory_kind: "physical_unit", line_id: uuid(8), sale_format: "unit", quantity: 1 },
    ],
    expected_total_amount: "2.01",
    expected_revision_hash: "a".repeat(64),
    reason_code: null,
    comment: null,
  },
};
const zeroQuote = {
  ...quote,
  total_amount: "0.00",
  lines: quote.lines.map((line) => ({
    ...line,
    discount_amount: line.gross_amount,
    net_amount: "0.00",
  })),
};
const zeroPending = {
  ...pending,
  version: 2,
  kind: "mixed_zero_return",
  request: {
    operation_id: pending.request.operation_id,
    items: pending.request.items,
    expected_revision_hash: pending.request.expected_revision_hash,
    reason_code: null,
    comment: null,
  },
};
const zeroCompleted = {
  version: 1,
  kind: "mixed_zero_return",
  state: "prepared_quarantine",
  operation_id: pending.request.operation_id,
  parent_sale_id: sale.id,
  tenant_id: sale.tenant_id,
  branch_id: sale.branch_id,
  actor_id: uuid(2),
  currency: "TJS",
  total_amount: "0.00",
  lines: [
    {
      inventory_kind: "legacy_pack",
      line_id: uuid(7),
      quantity: "0.001",
      offset: "0.000",
    },
    {
      inventory_kind: "physical_unit",
      line_id: uuid(8),
      sale_format: "unit",
      quantity: 1,
      base_units: 1,
      offset: 0,
    },
  ],
};
function ownShift(): void {
  mocks.shift.mockReturnValue({
    data: {
      id: sale.shift_id,
      status: "open",
      opened_by_user_id: uuid(2),
      branch_id: sale.branch_id,
      register_id: sale.register_id,
    },
  });
}
async function selectBoth(): Promise<void> {
  fireEvent.click(await screen.findByRole("checkbox", { name: "Дробные пачки" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "Физические таблетки" }));
}
async function calculate(): Promise<void> {
  await selectBoth();
  fireEvent.click(screen.getByRole("button", { name: "Рассчитать возврат" }));
  await screen.findByText("2.01 TJS");
}
describe("mixed refund dialog", () => {
  afterEach(cleanup);
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.active = true;
    mocks.load.mockReturnValue(null);
    mocks.options.mockResolvedValue(options);
    mocks.quote.mockResolvedValue(quote);
    mocks.inspect.mockResolvedValue({ state: "not_found" });
    mocks.shift.mockReturnValue({ data: null });
    mocks.ack.mockResolvedValue(true);
  });
  it("reviews both inventory kinds without an open shift, but cannot pay", async () => {
    render(<MixedRefundModal sale={sale} onClose={vi.fn()} onRefunded={vi.fn()} />);
    await calculate();
    expect(screen.getByText(/Можно вернуть: 2.000 уп/)).toBeInTheDocument();
    expect(screen.getByText(/Можно вернуть: 60 табл/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Оформить возврат наличными" })).toBeDisabled();
    expect(mocks.run).not.toHaveBeenCalled();
  });
  it.each(["legacy", "physical", "format"])(
    "invalidates review after changing %s",
    async (kind) => {
      ownShift();
      render(<MixedRefundModal sale={sale} onClose={vi.fn()} onRefunded={vi.fn()} />);
      await calculate();
      const confirm = screen.getByRole("button", { name: "Оформить возврат наличными" });
      expect(confirm).toBeEnabled();
      fireEvent.change(
        kind === "format"
          ? screen.getByLabelText("Что возвращают")
          : screen.getByLabelText(
              `Количество ${kind === "legacy" ? "Дробные пачки" : "Физические таблетки"}`,
            ),
        { target: { value: kind === "format" ? "blister" : kind === "legacy" ? "0.002" : "2" } },
      );
      expect(confirm).toBeDisabled();
      expect(screen.queryByText("2.01 TJS")).not.toBeInTheDocument();
      expect(mocks.run).not.toHaveBeenCalled();
    },
  );
  it.each(["legacy precision", "physical fraction", "remaining", "step"])(
    "rejects invalid %s before requesting a quote",
    async (kind) => {
      if (kind === "step")
        mocks.options.mockResolvedValue({
          ...options,
          lines: [
            options.lines[0],
            { ...options.lines[1], packaging: { ...options.lines[1].packaging, dispense_step: 2 } },
          ],
        });
      render(<MixedRefundModal sale={sale} onClose={vi.fn()} onRefunded={vi.fn()} />);
      await selectBoth();
      const label =
        kind === "legacy precision" ? "Количество Дробные пачки" : "Количество Физические таблетки";
      fireEvent.change(screen.getByLabelText(label), {
        target: {
          value:
            kind === "legacy precision"
              ? "0.0001"
              : kind === "physical fraction"
                ? "1.5"
                : kind === "remaining"
                  ? "61"
                  : "1",
        },
      });
      fireEvent.click(screen.getByRole("button", { name: "Рассчитать возврат" }));
      await screen.findByRole("alert");
      expect(mocks.quote).not.toHaveBeenCalled();
    },
  );
  it("requires current reason policy and passes canonical reviewed items", async () => {
    ownShift();
    mocks.options.mockResolvedValue({ ...options, reason_mode: "required_with_text" });
    mocks.run.mockResolvedValue(completed);
    render(<MixedRefundModal sale={sale} onClose={vi.fn()} onRefunded={vi.fn()} />);
    await calculate();
    fireEvent.click(screen.getByRole("button", { name: "Оформить возврат наличными" }));
    await screen.findByText("Выберите причину возврата");
    expect(mocks.run).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText(/Причина возврата/), { target: { value: "other" } });
    fireEvent.click(screen.getByRole("button", { name: "Оформить возврат наличными" }));
    await screen.findByText("Кратко поясните причину возврата");
    fireEvent.change(screen.getByLabelText(/Пояснение/), { target: { value: "  Ошибка  " } });
    fireEvent.click(screen.getByRole("button", { name: "Оформить возврат наличными" }));
    await screen.findByText("Возврат оформлен");
    expect(mocks.run).toHaveBeenCalledWith(
      expect.anything(),
      expect.objectContaining({
        items: pending.request.items,
        expected_total_amount: "2.01",
        expected_revision_hash: "a".repeat(64),
        reason_code: "other",
        comment: "Ошибка",
        shift_id: sale.shift_id,
      }),
    );
  });
  it("never pays automatically when reopening a pending operation", async () => {
    mocks.load.mockReturnValue(pending);
    render(<MixedRefundModal sale={sale} onClose={vi.fn()} onRefunded={vi.fn()} />);
    await screen.findByText("Проверяем ранее отправленный возврат");
    await waitFor(() => expect(mocks.inspect).toHaveBeenCalledTimes(1));
    expect(mocks.options).not.toHaveBeenCalled();
    expect(mocks.run).not.toHaveBeenCalled();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Проверить результат" }));
    await waitFor(() => expect(mocks.inspect).toHaveBeenCalledTimes(2));
    expect(mocks.run).not.toHaveBeenCalled();
  });
  it("blocks corrupted storage without starting another operation", async () => {
    mocks.load.mockImplementation(() => {
      throw new Error("Сохранённый возврат повреждён");
    });
    render(<MixedRefundModal sale={sale} onClose={vi.fn()} onRefunded={vi.fn()} />);
    await screen.findByText("Сохранённый возврат повреждён");
    expect(mocks.options).not.toHaveBeenCalled();
    expect(mocks.run).not.toHaveBeenCalled();
  });
  it("receives a reviewed zero amount without an open shift or cash payout", async () => {
    mocks.quote.mockResolvedValue(zeroQuote);
    mocks.runZero.mockResolvedValue(zeroCompleted);
    render(<MixedRefundModal sale={sale} onClose={vi.fn()} onRefunded={vi.fn()} />);
    await selectBoth();
    fireEvent.click(screen.getByRole("button", { name: "Рассчитать возврат" }));
    await screen.findByText(/Открытая смена не требуется/);
    const receive = screen.getByRole("button", { name: "Принять в карантин без выплаты" });
    expect(receive).toBeEnabled();
    expect(screen.queryByRole("button", { name: "Оформить возврат наличными" })).toBeNull();
    fireEvent.click(receive);
    await screen.findByText("Товар принят в карантин");
    expect(mocks.runZero).toHaveBeenCalledWith(
      expect.anything(),
      {
        items: pending.request.items,
        expected_revision_hash: quote.revision_hash,
        reason_code: null,
        comment: null,
      },
      zeroQuote,
    );
    expect(mocks.run).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "Открыть чек возврата" })).toBeNull();
  });
  it("enforces the current reason policy for a zero-value intake", async () => {
    mocks.options.mockResolvedValue({ ...options, reason_mode: "required_with_text" });
    mocks.quote.mockResolvedValue(zeroQuote);
    mocks.runZero.mockResolvedValue(zeroCompleted);
    render(<MixedRefundModal sale={sale} onClose={vi.fn()} onRefunded={vi.fn()} />);
    await selectBoth();
    fireEvent.click(screen.getByRole("button", { name: "Рассчитать возврат" }));
    const receive = await screen.findByRole("button", { name: "Принять в карантин без выплаты" });
    fireEvent.click(receive);
    await screen.findByText("Выберите причину возврата");
    expect(mocks.runZero).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText(/Причина возврата/), { target: { value: "other" } });
    fireEvent.click(receive);
    await screen.findByText("Кратко поясните причину возврата");
    expect(mocks.runZero).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText(/Пояснение/), { target: { value: "  Приём  " } });
    fireEvent.click(receive);
    await screen.findByText("Товар принят в карантин");
    expect(mocks.runZero).toHaveBeenCalledWith(
      expect.anything(),
      expect.objectContaining({ reason_code: "other", comment: "Приём" }),
      zeroQuote,
    );
  });
  it("only inspects a pending zero intake and explicitly retries the same kind", async () => {
    mocks.load.mockReturnValue(zeroPending);
    mocks.runZero.mockResolvedValue(zeroCompleted);
    render(<MixedRefundModal sale={sale} onClose={vi.fn()} onRefunded={vi.fn()} />);
    await screen.findByText(/Карантин без выплаты денег\./);
    const retry = screen.getByRole("button", { name: "Повторить эту операцию" });
    await waitFor(() => expect(retry).toBeEnabled());
    expect(mocks.runZero).not.toHaveBeenCalled();
    expect(mocks.run).not.toHaveBeenCalled();
    expect(screen.queryByRole("checkbox")).toBeNull();
    fireEvent.click(retry);
    await screen.findByText("Товар принят в карантин");
    expect(mocks.runZero).toHaveBeenCalledWith(expect.anything());
    expect(mocks.run).not.toHaveBeenCalled();
  });
  it.each([true, false])(
    "acknowledges quarantine without opening a payment receipt: %s",
    async (ack) => {
      mocks.load.mockReturnValue(zeroPending);
      mocks.inspect.mockResolvedValue(zeroCompleted);
      mocks.ack.mockResolvedValue(ack);
      const onRefunded = vi.fn();
      const onClose = vi.fn();
      render(<MixedRefundModal sale={sale} onClose={onClose} onRefunded={onRefunded} />);
      const done = await screen.findByRole("button", { name: "Готово" });
      await waitFor(() => expect(done).toBeEnabled());
      expect(onRefunded).not.toHaveBeenCalled();
      fireEvent.click(done);
      await waitFor(() => expect(mocks.ack).toHaveBeenCalledTimes(1));
      if (ack) {
        await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
        expect(onRefunded).toHaveBeenCalledWith(sale.id);
      } else {
        await screen.findByText(/Не удалось подтвердить сохранённый результат/);
        expect(onClose).not.toHaveBeenCalled();
        expect(onRefunded).not.toHaveBeenCalled();
      }
      expect(mocks.run).not.toHaveBeenCalled();
      expect(mocks.runZero).not.toHaveBeenCalled();
    },
  );
  it.each(["closed", "actor", "branch", "register"])(
    "does not pay with a mismatched shift: %s",
    async (kind) => {
      ownShift();
      const data = { ...mocks.shift().data };
      if (kind === "closed") data.status = "closed";
      else
        data[
          kind === "actor" ? "opened_by_user_id" : kind === "branch" ? "branch_id" : "register_id"
        ] = uuid(99);
      mocks.shift.mockReturnValue({ data });
      render(<MixedRefundModal sale={sale} onClose={vi.fn()} onRefunded={vi.fn()} />);
      await calculate();
      expect(screen.getByRole("button", { name: "Оформить возврат наличными" })).toBeDisabled();
      expect(mocks.run).not.toHaveBeenCalled();
    },
  );
  it("requires an explicit verified reset after a definite rejection", async () => {
    mocks.load.mockReturnValue({ ...pending, definiteRejected: true });
    mocks.abandon.mockImplementation(async () => {
      mocks.load.mockReturnValue(null);
      return { state: "cleared" };
    });
    render(<MixedRefundModal sale={sale} onClose={vi.fn()} onRefunded={vi.fn()} />);
    const reset = await screen.findByRole("button", { name: "Пересчитать возврат" });
    await waitFor(() => expect(reset).toBeEnabled());
    expect(mocks.abandon).not.toHaveBeenCalled();
    fireEvent.click(reset);
    await screen.findByRole("checkbox", { name: "Дробные пачки" });
    expect(mocks.abandon).toHaveBeenCalledTimes(1);
    expect(mocks.run).not.toHaveBeenCalled();
  });
  it.each([true, false])("opens a receipt only after verified acknowledgement: %s", async (ack) => {
    mocks.load.mockReturnValue(pending);
    mocks.inspect.mockResolvedValue(completed);
    mocks.ack.mockResolvedValue(ack);
    const onRefunded = vi.fn();
    render(<MixedRefundModal sale={sale} onClose={vi.fn()} onRefunded={onRefunded} />);
    fireEvent.click(await screen.findByRole("button", { name: "Открыть чек возврата" }));
    await waitFor(() => expect(mocks.ack).toHaveBeenCalled());
    if (ack) {
      await waitFor(() => expect(onRefunded).toHaveBeenCalledWith(uuid(10)));
      expect(mocks.invalidate).toHaveBeenCalledTimes(3);
    } else {
      await screen.findByRole("alert");
      expect(onRefunded).not.toHaveBeenCalled();
      expect(mocks.invalidate).not.toHaveBeenCalled();
    }
  });
  it("does not publish a late quote after changing account", async () => {
    let finish: (value: typeof quote) => void = () => undefined;
    mocks.quote.mockReturnValue(
      new Promise<typeof quote>((resolve) => {
        finish = resolve;
      }),
    );
    render(<MixedRefundModal sale={sale} onClose={vi.fn()} onRefunded={vi.fn()} />);
    await selectBoth();
    fireEvent.click(screen.getByRole("button", { name: "Рассчитать возврат" }));
    await waitFor(() => expect(mocks.quote).toHaveBeenCalled());
    mocks.active = false;
    finish(quote);
    await waitFor(() => expect(screen.queryByText("2.01 TJS")).not.toBeInTheDocument());
    expect(mocks.run).not.toHaveBeenCalled();
  });
  it("enables an exact retry after a temporary lookup failure followed by not_found", async () => {
    mocks.load.mockReturnValue(pending);
    mocks.inspect
      .mockRejectedValueOnce(new Error("Временно нет связи"))
      .mockResolvedValue({ state: "not_found" });
    render(<MixedRefundModal sale={sale} onClose={vi.fn()} onRefunded={vi.fn()} />);
    await screen.findByText("Временно нет связи");
    const retry = screen.getByRole("button", { name: "Повторить эту операцию" });
    expect(retry).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Проверить результат" }));
    await waitFor(() => expect(retry).toBeEnabled());
    expect(mocks.run).not.toHaveBeenCalled();
  });
  it("discards a late quote after another tab refreshes the pending operation", async () => {
    ownShift();
    let finish: (value: typeof quote) => void = () => undefined;
    mocks.quote.mockReturnValue(
      new Promise<typeof quote>((resolve) => {
        finish = resolve;
      }),
    );
    render(<MixedRefundModal sale={sale} onClose={vi.fn()} onRefunded={vi.fn()} />);
    await selectBoth();
    fireEvent.click(screen.getByRole("button", { name: "Рассчитать возврат" }));
    await waitFor(() => expect(mocks.quote).toHaveBeenCalled());
    fireEvent(window, new StorageEvent("storage", { key: null }));
    await waitFor(() => expect(mocks.options).toHaveBeenCalledTimes(2));
    await waitFor(() =>
      expect(screen.getByRole("checkbox", { name: "Дробные пачки" })).not.toBeChecked(),
    );
    await act(async () => finish(quote));
    await selectBoth();
    expect(screen.queryByText("2.01 TJS")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Оформить возврат наличными" })).toBeDisabled();
    expect(mocks.run).not.toHaveBeenCalled();
  });
});
