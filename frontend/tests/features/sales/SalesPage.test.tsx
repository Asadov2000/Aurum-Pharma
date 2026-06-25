import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const listSales = vi.fn();
const getSaleDetails = vi.fn();
const refundSale = vi.fn();

vi.mock("@/features/sales/api", () => ({
  listSales: (...a: unknown[]) => listSales(...a),
  getSaleDetails: (...a: unknown[]) => getSaleDetails(...a),
  refundSale: (...a: unknown[]) => refundSale(...a),
}));

vi.mock("@/features/foundation/api", () => ({
  listBranches: vi.fn().mockResolvedValue([]),
  listRegisters: vi.fn().mockResolvedValue([]),
}));

vi.mock("@/features/auth/hooks", () => ({
  useAuth: () => ({ user: { home_tenant_id: "t-1", is_developer: false } }),
}));

import { SalesPage } from "@/features/sales/SalesPage";

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <SalesPage />
    </QueryClientProvider>,
  );
}

const SALE = {
  id: "s-1",
  receipt_number: "000142",
  completed_at: "2026-05-28T10:00:00Z",
  branch_name: "Аптека №1",
  register_name: "Касса 1",
  cashier_name: "Иван Кассиров",
  total_amount: "120.00",
  currency: "TJS",
  payment_methods: ["cash"],
  is_refund: false,
  parent_sale_id: null,
  parent_receipt_number: null,
  has_refund: true,
  refund_receipt_number: "000143",
  items_summary: "Парацетамол x2",
  status: "completed",
};

describe("SalesPage", () => {
  beforeEach(() => {
    listSales.mockReset();
    getSaleDetails.mockReset();
    refundSale.mockReset();
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders an empty state when there are no receipts", async () => {
    listSales.mockResolvedValueOnce({ items: [], total: 0, page: 1, page_size: 50 });
    renderPage();
    expect(await screen.findByText(/Чеков пока нет/i)).toBeInTheDocument();
  });

  it("renders a receipt row with resolved cashier name and refund badge", async () => {
    listSales.mockResolvedValueOnce({ items: [SALE], total: 1, page: 1, page_size: 50 });
    renderPage();
    expect(await screen.findByText("000142")).toBeInTheDocument();
    expect(screen.getByText("Иван Кассиров")).toBeInTheDocument();
    expect(screen.getByText("Наличные")).toBeInTheDocument();
    expect(screen.getByText(/Есть возврат/)).toBeInTheDocument();
    expect(screen.getByText(/всего: 1/)).toBeInTheDocument();
  });

  it("re-queries with the receipt_number filter typed in", async () => {
    listSales.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 50 });
    renderPage();
    await screen.findByText(/Чеков пока нет/i);
    fireEvent.change(screen.getByLabelText("№ чека"), { target: { value: "000142" } });
    await waitFor(() => {
      expect(listSales).toHaveBeenLastCalledWith(
        expect.objectContaining({ receipt_number: "000142", page: 1 }),
      );
    });
  });

  it("opens the detail modal on row click and loads full receipt", async () => {
    listSales.mockResolvedValueOnce({ items: [SALE], total: 1, page: 1, page_size: 50 });
    getSaleDetails.mockResolvedValueOnce({
      id: "s-1",
      sale_type: "sale",
      status: "completed",
      receipt_number: "000142",
      completed_at: "2026-05-28T10:00:00Z",
      total_amount: "120.00",
      currency: "TJS",
      parent_sale_id: null,
      items: [
        {
          id: "i-1",
          sale_id: "s-1",
          catalog_id: "cat-1",
          batch_id: "b-1",
          qty: "2",
          unit_price: "60.00",
          total_price: "120.00",
          currency: "TJS",
          discount_amount: "0",
          position: 1,
        },
      ],
      payments: [
        { id: "p-1", sale_id: "s-1", payment_method: "cash", amount: "120.00", currency: "TJS" },
      ],
    });
    renderPage();
    fireEvent.click(await screen.findByText("000142"));
    expect(await screen.findByText(/Чек № 000142/)).toBeInTheDocument();
    await waitFor(() => {
      expect(getSaleDetails).toHaveBeenCalledWith("s-1");
    });
    // Refund button hidden because this row already has_refund.
    expect(screen.queryByRole("button", { name: /Оформить возврат/ })).not.toBeInTheDocument();
  });
});
