import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PhysicalRefundModal } from "@/features/sales/PhysicalRefundModal";
import { type SaleDetails } from "@/features/pos/types";

const mocks = vi.hoisted(() => ({
  options: vi.fn(),
  quote: vi.fn(),
  load: vi.fn(),
  inspect: vi.fn(),
  run: vi.fn(),
  ack: vi.fn(),
  shift: vi.fn(),
}));
const uuid = (value: number): string =>
  `10000000-0000-4000-8000-${String(value).padStart(12, "0")}`;
vi.mock("@/features/pos/posStorageScope", () => ({
  currentPosStorageScope: () => ({
    tenantId: "10000000-0000-4000-8000-000000000001",
    userId: "10000000-0000-4000-8000-000000000002",
  }),
  capturePosSessionGuard: () => () => true,
}));
vi.mock("@/features/pos/queries", () => ({ useCurrentShiftQuery: mocks.shift }));
vi.mock("@/features/sales/physicalRefund", async (original) => ({
  ...(await original<typeof import("@/features/sales/physicalRefund")>()),
  getPhysicalRefundOptions: mocks.options,
  quotePhysicalRefund: mocks.quote,
}));
vi.mock("@/features/sales/physicalRefundRecovery", async (original) => ({
  ...(await original<typeof import("@/features/sales/physicalRefundRecovery")>()),
  loadPhysicalRefund: mocks.load,
  inspectPhysicalRefund: mocks.inspect,
  runPhysicalRefund: mocks.run,
  acknowledgePhysicalRefund: mocks.ack,
}));
const sale: SaleDetails = {
  id: uuid(3),
  tenant_id: uuid(1),
  branch_id: uuid(4),
  register_id: uuid(5),
  shift_id: uuid(6),
  sale_type: "sale",
  parent_sale_id: null,
  status: "completed",
  receipt_number: "R-1",
  is_test: false,
  total_amount: "60.00",
  currency: "TJS",
  voided_at: null,
  voided_by_sale_id: null,
  cashier_user_id: uuid(2),
  created_at: "2026-10-09T08:30:00Z",
  completed_at: "2026-10-09T08:30:00Z",
  items: [],
  payments: [],
};
const options = {
  parent_sale_id: sale.id,
  branch_id: sale.branch_id,
  register_id: sale.register_id,
  original_receipt_number: "R-1",
  reason_mode: "optional",
  lines: [
    {
      line_id: uuid(7),
      name: "Парацетамол 500 мг",
      unit_label: "табл.",
      packaging: {
        version: 2,
        unit: "tablet",
        units_per_pack: 30,
        units_per_blister: 10,
        dispense_step: 1,
      },
      sold_base_units: 30,
      returned_base_units: 0,
      remaining_base_units: 30,
      original_sale_format: "pack",
      original_retail_unit_price: "60.00",
    },
  ],
};
describe("physical refund dialog safe recovery", () => {
  afterEach(cleanup);
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.load.mockReturnValue(null);
    mocks.options.mockResolvedValue(options);
    mocks.inspect.mockResolvedValue({ state: "not_found" });
    mocks.shift.mockReturnValue({ data: null });
  });
  it("shows available units and formats, while submission requires the cashier's open shift", async () => {
    render(<PhysicalRefundModal sale={sale} onClose={vi.fn()} onRefunded={vi.fn()} />);
    await screen.findByText("Парацетамол 500 мг");
    expect(screen.getByText(/Можно вернуть: 30 табл/)).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Блистеры" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Оформить возврат наличными" })).toBeDisabled();
    expect(mocks.run).not.toHaveBeenCalled();
  });
  it.each(["quantity", "format"])(
    "invalidates a server quote immediately after changing %s without sending a payout",
    async (field) => {
      mocks.shift.mockReturnValue({
        data: {
          id: uuid(6),
          status: "open",
          opened_by_user_id: uuid(2),
          branch_id: sale.branch_id,
          register_id: sale.register_id,
        },
      });
      mocks.quote.mockResolvedValue({
        parent_sale_id: sale.id,
        revision_hash: "a".repeat(64),
        total_amount: "2.00",
        currency: "TJS",
        lines: [
          {
            line_id: uuid(7),
            sale_format: "unit",
            quantity: 1,
            base_units: 1,
            offset: 0,
            gross_amount: "2.00",
            discount_amount: "0.00",
            net_amount: "2.00",
          },
        ],
      });
      render(<PhysicalRefundModal sale={sale} onClose={vi.fn()} onRefunded={vi.fn()} />);
      fireEvent.click(await screen.findByRole("checkbox", { name: "Парацетамол 500 мг" }));
      fireEvent.click(screen.getByRole("button", { name: "Рассчитать возврат" }));
      const confirm = screen.getByRole("button", { name: "Оформить возврат наличными" });
      await waitFor(() => expect(confirm).toBeEnabled());
      expect(screen.getByText("2.00 TJS")).toBeInTheDocument();
      if (field === "quantity") {
        fireEvent.change(screen.getByLabelText("Количество"), { target: { value: "2" } });
      } else {
        fireEvent.change(screen.getByLabelText("Что возвращают"), { target: { value: "blister" } });
      }
      expect(confirm).toBeDisabled();
      expect(screen.queryByText("2.00 TJS")).not.toBeInTheDocument();
      expect(mocks.quote).toHaveBeenCalledTimes(1);
      expect(mocks.run).not.toHaveBeenCalled();
    },
  );
  it("reload inspection never auto-submits a persisted payout and locks editing", async () => {
    mocks.load.mockReturnValue({
      version: 1,
      tenantId: uuid(1),
      userId: uuid(2),
      parentSaleId: sale.id,
      branchId: sale.branch_id,
      registerId: sale.register_id,
      definiteRejected: false,
      request: {
        operation_id: uuid(8),
        shift_id: uuid(6),
        items: [{ line_id: uuid(7), sale_format: "unit", quantity: 2 }],
        expected_total_amount: "4.00",
        expected_revision_hash: "a".repeat(64),
        reason_code: null,
        comment: null,
      },
    });
    render(<PhysicalRefundModal sale={sale} onClose={vi.fn()} onRefunded={vi.fn()} />);
    await screen.findByText("Проверяем ранее отправленный возврат");
    await waitFor(() => expect(mocks.inspect).toHaveBeenCalledTimes(1));
    expect(mocks.options).not.toHaveBeenCalled();
    expect(mocks.run).not.toHaveBeenCalled();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Проверить результат" }));
    await waitFor(() => expect(mocks.inspect).toHaveBeenCalledTimes(2));
    expect(mocks.run).not.toHaveBeenCalled();
  });
  it("offers recalculation immediately when retry receives a definitive server rejection", async () => {
    const stored = {
      version: 1,
      tenantId: uuid(1),
      userId: uuid(2),
      parentSaleId: sale.id,
      branchId: sale.branch_id,
      registerId: sale.register_id,
      definiteRejected: false,
      request: {
        operation_id: uuid(8),
        shift_id: uuid(6),
        items: [{ line_id: uuid(7), sale_format: "unit", quantity: 2 }],
        expected_total_amount: "4.00",
        expected_revision_hash: "a".repeat(64),
        reason_code: null,
        comment: null,
      },
    };
    mocks.load.mockReturnValue(stored);
    mocks.run.mockImplementationOnce(() => {
      mocks.load.mockReturnValue({ ...stored, definiteRejected: true });
      return Promise.reject(new Error("Расчёт возврата устарел."));
    });
    render(<PhysicalRefundModal sale={sale} onClose={vi.fn()} onRefunded={vi.fn()} />);
    await screen.findByText("Проверяем ранее отправленный возврат");
    expect(screen.queryByRole("button", { name: "Пересчитать возврат" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Повторить эту операцию" }));
    expect(await screen.findByRole("button", { name: "Пересчитать возврат" })).toBeEnabled();
    expect(mocks.run).toHaveBeenCalledTimes(1);
    expect(mocks.options).not.toHaveBeenCalled();
  });
  it("a malformed journal blocks a new operation and remains visible as an error", async () => {
    mocks.load.mockImplementation(() => {
      throw new Error("Сохранённый возврат повреждён");
    });
    render(<PhysicalRefundModal sale={sale} onClose={vi.fn()} onRefunded={vi.fn()} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("повреждён");
    expect(screen.getByRole("button", { name: "Оформить возврат наличными" })).toBeDisabled();
    expect(mocks.options).not.toHaveBeenCalled();
    expect(mocks.run).not.toHaveBeenCalled();
  });
});
