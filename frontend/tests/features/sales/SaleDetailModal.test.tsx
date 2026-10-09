import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { type SaleDetails } from "@/features/pos/types";
import { SaleDetailModal } from "@/features/sales/SaleDetailModal";
import { type SaleListItem } from "@/features/sales/types";

const details = vi.hoisted(() => ({ data: undefined as SaleDetails | undefined }));

vi.mock("@/features/auth/hooks", () => ({
  useAuth: () => ({ user: { is_developer: false, permissions: ["pos.refund"] } }),
}));
vi.mock("@/features/sales/queries", () => ({
  useSaleDetailsQuery: () => ({ ...details, isLoading: false, error: null }),
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

describe("SaleDetailModal physical receipt", () => {
  beforeEach(() => {
    details.data = { ...sale };
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
