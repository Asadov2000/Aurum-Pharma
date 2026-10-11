import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { type SaleDetails } from "@/features/pos/types";
import { SaleDetailModal } from "@/features/sales/SaleDetailModal";
import { type SaleListItem } from "@/features/sales/types";

const details = vi.hoisted(() => ({ data: undefined as SaleDetails | undefined }));
const auth = vi.hoisted(() => ({ permissions: ["pos.refund"] }));

vi.mock("@/features/auth/hooks", () => ({
  useAuth: () => ({ user: { is_developer: false, permissions: auth.permissions } }),
}));
vi.mock("@/features/sales/queries", () => ({
  useSaleDetailsQuery: () => ({ ...details, isLoading: false, error: null }),
}));
vi.mock("@/features/sales/RefundModal", () => ({
  RefundModal: () => <div role="dialog" aria-label="Возврат обычной продажи" />,
}));
vi.mock("@/features/sales/PhysicalRefundModal", () => ({
  PhysicalRefundModal: () => <div role="dialog" aria-label="Возврат поштучной продажи" />,
}));
vi.mock("@/features/pos/ReceiptPrintModal", () => ({
  ReceiptPrintModal: ({ saleId, registerId }: { saleId: string; registerId: string }) => (
    <div role="dialog" aria-label="Печать">
      {saleId} / {registerId}
    </div>
  ),
}));

const row: SaleListItem = {
  id: "sale-1",
  receipt_number: "000142",
  completed_at: "2026-10-07T10:00:00Z",
  branch_name: "Аптека №1",
  register_name: "Касса 1",
  cashier_name: "Кассир",
  total_amount: "20.00",
  currency: "TJS",
  payment_methods: ["cash"],
  is_refund: false,
  parent_sale_id: null,
  parent_receipt_number: null,
  has_refund: false,
  refund_receipt_number: null,
  items_summary: "Лекарство",
  status: "completed",
};

const sale: SaleDetails = {
  id: row.id,
  tenant_id: "tenant-1",
  branch_id: "branch-1",
  register_id: "register-1",
  shift_id: "shift-1",
  sale_type: "sale",
  parent_sale_id: null,
  status: "completed",
  receipt_number: row.receipt_number,
  is_test: false,
  total_amount: "20.00",
  currency: "TJS",
  voided_at: null,
  voided_by_sale_id: null,
  cashier_user_id: "cashier-1",
  created_at: "2026-10-07T10:00:00Z",
  completed_at: row.completed_at,
  items: [],
  payments: [],
};

const legacyItem: SaleDetails["items"][number] = {
  id: "item-1",
  sale_id: sale.id,
  catalog_id: "catalog-1",
  batch_id: "batch-1",
  qty: "2",
  unit_price: "10.00",
  total_price: "20.00",
  currency: "TJS",
  discount_amount: "0.00",
  position: 1,
  name: "Дублирующая обычная строка",
  batch_number: "DUPLICATE",
  refunded_qty: "1",
};

const physicalItem: NonNullable<SaleDetails["physical_items"]>[number] = {
  position: 1,
  name: "Дублирующая поштучная строка",
  qty: "2",
  unit_price: "10.00",
  total_price: "20.00",
  discount_amount: "0.00",
  unit_label: "блист.",
};

const mixedItems: NonNullable<SaleDetails["mixed_items"]> = [
  {
    position: 1,
    name: "Первый поштучный товар",
    qty: "2",
    unit_label: "блист.",
    quantity_description: "2 блистера по 10 табл.",
    unit_price: "10.00",
    discount_amount: "0.00",
    total_price: "20.00",
  },
  {
    position: 5,
    name: "Обычный товар между поштучными",
    qty: "0.125",
    unit_label: "уп.",
    quantity_description: null,
    unit_price: "40.00",
    discount_amount: "0.00",
    total_price: "5.00",
  },
  {
    position: 9,
    name: "Последний поштучный товар",
    qty: "3",
    unit_label: "табл.",
    quantity_description: "3 отдельные таблетки",
    unit_price: "1.00",
    discount_amount: "0.00",
    total_price: "3.00",
  },
];

describe("SaleDetailModal physical receipt", () => {
  beforeEach(() => {
    details.data = { ...sale };
    auth.permissions = ["pos.refund"];
  });

  it("shows physical units and allows the dedicated refund action without invented batch data", () => {
    details.data = {
      ...sale,
      physical_items: [
        {
          position: 1,
          name: "Парацетамол 500 мг",
          qty: "2",
          unit_price: "10.00",
          total_price: "20.00",
          discount_amount: "0.00",
          unit_label: "блист.",
          quantity_description: "2 блистера по 10 табл.",
        },
      ],
    };
    render(<SaleDetailModal row={row} onClose={vi.fn()} />);
    expect(screen.getByText("Парацетамол 500 мг")).toBeInTheDocument();
    expect(screen.getByText("2 блистера по 10 табл.")).toBeInTheDocument();
    expect(screen.getByText("2 блист.")).toBeInTheDocument();
    expect(screen.queryByText(/Партия/)).not.toBeInTheDocument();
    expect(screen.queryByRole("columnheader", { name: "Возвращено" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Оформить возврат" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Печать чека" })).toBeInTheDocument();
  });

  it("keeps legacy batch, returned quantity and refund action", () => {
    details.data = {
      ...sale,
      items: [
        {
          id: "item-1",
          sale_id: sale.id,
          catalog_id: "catalog-1",
          batch_id: "batch-1",
          qty: "2",
          unit_price: "10.00",
          total_price: "20.00",
          currency: "TJS",
          discount_amount: "0.00",
          position: 1,
          name: "Лекарство",
          batch_number: "B-123",
          refunded_qty: "1",
        },
      ],
    };
    render(<SaleDetailModal row={row} onClose={vi.fn()} />);
    expect(screen.getByText("Партия B-123")).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Возвращено" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Оформить возврат" })).toBeInTheDocument();
  });
});

describe("SaleDetailModal mixed receipt", () => {
  beforeEach(() => {
    auth.permissions = ["pos.refund"];
    details.data = { ...sale, total_amount: "28.00", mixed_items: mixedItems };
  });

  it("preserves interleaved global order, fractional quantities and receipt prices", () => {
    render(<SaleDetailModal row={row} onClose={vi.fn()} />);

    const lines = screen.getAllByRole("row").slice(1);
    expect(lines).toHaveLength(3);
    for (const [index, item] of mixedItems.entries()) {
      expect(within(lines[index]).getByText(item.name)).toBeInTheDocument();
      expect(within(lines[index]).getAllByRole("cell")).toHaveLength(4);
    }
    expect(within(lines[0]).getByText("2 блист.")).toBeInTheDocument();
    expect(within(lines[0]).getByText("2 блистера по 10 табл.")).toBeInTheDocument();
    expect(within(lines[0]).getByText("10,00")).toBeInTheDocument();
    expect(within(lines[0]).getByText("20,00")).toBeInTheDocument();
    expect(within(lines[1]).getByText("0,125 уп.")).toBeInTheDocument();
    expect(within(lines[1]).getByText("40,00")).toBeInTheDocument();
    expect(within(lines[1]).getByText("5,00")).toBeInTheDocument();
    expect(within(lines[2]).getByText("3 табл.")).toBeInTheDocument();
    expect(within(lines[2]).getByText("3 отдельные таблетки")).toBeInTheDocument();
    expect(screen.queryByRole("columnheader", { name: "Возвращено" })).not.toBeInTheDocument();
  });

  it("ignores duplicate legacy and physical arrays when mixed lines exist", () => {
    details.data = {
      ...sale,
      mixed_items: mixedItems,
      items: [legacyItem],
      physical_items: [physicalItem],
    };
    render(<SaleDetailModal row={row} onClose={vi.fn()} />);

    expect(screen.getAllByRole("row")).toHaveLength(4);
    expect(screen.queryByText("Дублирующая обычная строка")).not.toBeInTheDocument();
    expect(screen.queryByText(physicalItem.name)).not.toBeInTheDocument();
    expect(screen.queryByText(/Партия/)).not.toBeInTheDocument();
    expect(screen.queryByText("Возвращено частично")).not.toBeInTheDocument();
    expect(screen.queryByRole("columnheader", { name: "Возвращено" })).not.toBeInTheDocument();
  });

  it.each([{ permissions: ["pos.refund"] }, { permissions: [] }])(
    "does not offer a mixed refund with permissions $permissions",
    ({ permissions }) => {
      auth.permissions = permissions;
      details.data = {
        ...sale,
        mixed_items: mixedItems,
        items: [legacyItem],
        physical_items: [physicalItem],
      };
      render(<SaleDetailModal row={row} onClose={vi.fn()} />);

      expect(screen.queryByRole("button", { name: "Оформить возврат" })).not.toBeInTheDocument();
      expect(screen.queryByRole("dialog", { name: /Возврат .* продажи/ })).not.toBeInTheDocument();
    },
  );

  it("keeps legacy rendering and refund availability for an empty mixed list", () => {
    details.data = { ...sale, mixed_items: [], items: [legacyItem] };
    render(<SaleDetailModal row={row} onClose={vi.fn()} />);

    expect(screen.getByText("Дублирующая обычная строка")).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Возвращено" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Оформить возврат" })).toBeInTheDocument();
  });

  it.each(["legacy", "physical"] as const)(
    "unmounts an already opened %s refund when mixed details arrive",
    async (inventory) => {
      details.data = {
        ...sale,
        items: inventory === "legacy" ? [legacyItem] : [],
        physical_items: inventory === "physical" ? [physicalItem] : [],
      };
      const { rerender } = render(<SaleDetailModal row={row} onClose={vi.fn()} />);
      fireEvent.click(screen.getByRole("button", { name: "Оформить возврат" }));
      expect(
        await screen.findByRole("dialog", {
          name: inventory === "legacy" ? "Возврат обычной продажи" : "Возврат поштучной продажи",
        }),
      ).toBeInTheDocument();

      details.data = { ...details.data, mixed_items: mixedItems };
      rerender(<SaleDetailModal row={row} onClose={vi.fn()} />);

      expect(screen.queryByRole("dialog", { name: /Возврат .* продажи/ })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Оформить возврат" })).not.toBeInTheDocument();
      expect(screen.getByText(mixedItems[0].name)).toBeInTheDocument();
    },
  );

  it("uses the existing print flow for the mixed sale and register", () => {
    render(<SaleDetailModal row={row} onClose={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Печать чека" }));

    expect(screen.getByRole("dialog", { name: "Печать" })).toHaveTextContent("sale-1 / register-1");
  });
});
