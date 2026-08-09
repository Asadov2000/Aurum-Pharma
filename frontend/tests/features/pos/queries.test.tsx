import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { PropsWithChildren } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const createSale = vi.fn();
const getSale = vi.fn();
const addSaleItem = vi.fn();

vi.mock("@/features/pos/api", () => ({
  addPayment: vi.fn(),
  addPrescription: vi.fn(),
  addSaleItem: (...args: unknown[]) => addSaleItem(...args),
  closeShift: vi.fn(),
  completeSale: vi.fn(),
  createSale: (...args: unknown[]) => createSale(...args),
  deleteSaleItem: vi.fn(),
  getCurrentShift: vi.fn(),
  getReceipt: vi.fn(),
  getSale: (...args: unknown[]) => getSale(...args),
  openShift: vi.fn(),
  updateSaleItem: vi.fn(),
}));

import { posKeys, useAddSaleItem, useCreateSale, useSaleQuery } from "@/features/pos/queries";
import type { Sale, SaleDetails } from "@/features/pos/types";

const SALE: Sale = {
  id: "sale-1",
  tenant_id: "tenant-1",
  branch_id: "branch-1",
  register_id: "register-1",
  shift_id: "shift-1",
  sale_type: "sale",
  parent_sale_id: null,
  status: "draft",
  receipt_number: null,
  is_test: false,
  total_amount: "0.00",
  currency: "TJS",
  voided_at: null,
  voided_by_sale_id: null,
  cashier_user_id: "user-1",
  created_at: "2026-07-11T08:00:00Z",
  completed_at: null,
};

const OPERATION_ID = "10000000-0000-4000-8000-000000000001";

const SALE_WITH_ITEM: SaleDetails = {
  ...SALE,
  total_amount: "70.00",
  items: [
    {
      id: "item-1",
      sale_id: SALE.id,
      catalog_id: "catalog-1",
      batch_id: "batch-1",
      qty: "7",
      unit_price: "10.00",
      total_price: "70.00",
      currency: "TJS",
      discount_amount: "0.00",
      position: 1,
    },
  ],
  payments: [],
};

function setupQueryClient() {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: 30_000 },
      mutations: { retry: false },
    },
  });
  const wrapper = ({ children }: PropsWithChildren) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return { client, wrapper };
}

describe("POS queries", () => {
  beforeEach(() => {
    createSale.mockReset();
    getSale.mockReset();
    addSaleItem.mockReset();
  });

  it("seeds a fresh empty draft after lazy sale creation", async () => {
    createSale.mockResolvedValue(SALE);
    const { client, wrapper } = setupQueryClient();
    const { result } = renderHook(() => useCreateSale(), { wrapper });

    await act(() =>
      result.current.mutateAsync({
        registerId: SALE.register_id,
        operationId: OPERATION_ID,
      }),
    );

    expect(client.getQueryData(posKeys.sale(SALE.id))).toEqual({
      ...SALE,
      items: [],
      payments: [],
    });
  });

  it("waits for the canonical sale refresh after adding an item", async () => {
    addSaleItem.mockResolvedValue({
      items: SALE_WITH_ITEM.items,
      requires_prescription_log: false,
    });
    getSale.mockResolvedValue(SALE_WITH_ITEM);
    const { client, wrapper } = setupQueryClient();
    client.setQueryData(posKeys.sale(SALE.id), { ...SALE, items: [], payments: [] });
    const { result } = renderHook(
      () => ({ addItem: useAddSaleItem(), sale: useSaleQuery(SALE.id) }),
      { wrapper },
    );

    await act(() =>
      result.current.addItem.mutateAsync({
        saleId: SALE.id,
        catalogId: "catalog-1",
        qty: "7",
        operationId: OPERATION_ID,
      }),
    );

    await waitFor(() => expect(result.current.sale.data).toEqual(SALE_WITH_ITEM));
    expect(getSale).toHaveBeenCalledTimes(1);
  });
});
