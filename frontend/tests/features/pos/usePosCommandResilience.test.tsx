import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { type PropsWithChildren } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({
  addPayment: vi.fn(),
  addPosFavorite: vi.fn(),
  addPrescription: vi.fn(),
  addSaleItem: vi.fn(),
  checkoutSale: vi.fn(),
  beginPaymentAttemptReconciliation: vi.fn(),
  closeShift: vi.fn(),
  completeSale: vi.fn(),
  confirmPaymentAttempt: vi.fn(),
  createPaymentAttempt: vi.fn(),
  createSale: vi.fn(),
  deleteSaleItem: vi.fn(),
  getCurrentShift: vi.fn(),
  getPosCommandResult: vi.fn(),
  getPosFavorites: vi.fn(),
  getReceipt: vi.fn(),
  getSale: vi.fn(),
  openShift: vi.fn(),
  removePosFavorite: vi.fn(),
  updateSaleItem: vi.fn(),
  voidPaymentAttempt: vi.fn(),
}));

vi.mock("@/features/pos/api", () => apiMocks);

import { createPendingPosCommand, loadPendingPosCommand } from "@/features/pos/commandOperation";
import { usePosCommandResilience } from "@/features/pos/usePosCommandResilience";

const REGISTER_ID = "register-1";
const SALE_ID = "sale-1";
const ITEM_ID = "item-1";

const SALE = {
  id: SALE_ID,
  tenant_id: "tenant-1",
  branch_id: "branch-1",
  register_id: REGISTER_ID,
  shift_id: "shift-1",
  sale_type: "sale" as const,
  parent_sale_id: null,
  status: "draft" as const,
  receipt_number: null,
  operation_id: null,
  is_test: false,
  total_amount: "10.00",
  currency: "TJS",
  voided_at: null,
  voided_by_sale_id: null,
  cashier_user_id: "user-1",
  created_at: "2026-08-09T10:00:00Z",
  completed_at: null,
  items: [
    {
      id: ITEM_ID,
      sale_id: SALE_ID,
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
};

const ADD_RESULT = {
  items: SALE.items,
  requires_prescription_log: false,
};

const networkError = { isAxiosError: true, response: undefined };
const notFoundError = { isAxiosError: true, response: { status: 404 } };
const collisionError = { isAxiosError: true, response: { status: 409 } };

function setup() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const wrapper = ({ children }: PropsWithChildren) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  const onApplied = vi.fn();
  const hook = renderHook(() => usePosCommandResilience({ registerId: REGISTER_ID, onApplied }), {
    wrapper,
  });
  return { ...hook, client, onApplied };
}

describe("usePosCommandResilience", () => {
  beforeEach(() => {
    window.localStorage.clear();
    Object.values(apiMocks).forEach((mock) => mock.mockReset());
    apiMocks.getSale.mockResolvedValue(SALE);
  });

  it("retries a lost response with the same operation id", async () => {
    apiMocks.addSaleItem.mockRejectedValueOnce(networkError).mockResolvedValueOnce(ADD_RESULT);
    apiMocks.getPosCommandResult.mockRejectedValue(notFoundError);
    const { result, onApplied } = setup();

    await act(() =>
      result.current.begin({
        commandType: "item.add",
        registerId: REGISTER_ID,
        saleId: SALE_ID,
        catalogId: "catalog-1",
        qty: "1",
        expiredSaleConfirmed: false,
      }),
    );

    await waitFor(() => expect(result.current.canRetry).toBe(true));
    const command = loadPendingPosCommand(REGISTER_ID);
    expect(command).not.toBeNull();
    expect(apiMocks.addSaleItem).toHaveBeenCalledTimes(1);
    expect(apiMocks.addSaleItem.mock.calls[0]?.[3]).toBe(command!.operationId);

    await act(() => result.current.retry());

    await waitFor(() => expect(result.current.blocked).toBe(false));
    expect(apiMocks.addSaleItem).toHaveBeenCalledTimes(2);
    expect(apiMocks.addSaleItem.mock.calls[1]?.[3]).toBe(command!.operationId);
    expect(onApplied).toHaveBeenCalledTimes(1);
    expect(loadPendingPosCommand(REGISTER_ID)).toBeNull();
  });

  it("restores a committed command after reload without replaying the mutation", async () => {
    const command = createPendingPosCommand({
      commandType: "item.add",
      registerId: REGISTER_ID,
      saleId: SALE_ID,
      catalogId: "catalog-1",
      qty: "1",
      expiredSaleConfirmed: false,
    });
    expect(command).not.toBeNull();
    apiMocks.getPosCommandResult.mockResolvedValue({
      operation_id: command!.operationId,
      sale_id: SALE_ID,
      created_at: SALE.created_at,
      result: { command_type: "item.add", item_add: ADD_RESULT },
    });

    const { result, onApplied } = setup();

    await waitFor(() => expect(result.current.blocked).toBe(false));
    expect(apiMocks.getPosCommandResult).toHaveBeenCalledWith(command!.operationId);
    expect(apiMocks.addSaleItem).not.toHaveBeenCalled();
    expect(onApplied).toHaveBeenCalledTimes(1);
    expect(loadPendingPosCommand(REGISTER_ID)).toBeNull();
  });

  it("blocks the next mutation while reconciliation is unresolved", async () => {
    apiMocks.updateSaleItem.mockRejectedValue(networkError);
    apiMocks.getPosCommandResult.mockRejectedValue(notFoundError);
    const { result } = setup();

    await act(() =>
      result.current.begin({
        commandType: "item.update",
        registerId: REGISTER_ID,
        saleId: SALE_ID,
        itemId: ITEM_ID,
        qty: "2",
      }),
    );
    await waitFor(() => expect(result.current.blocked).toBe(true));

    const second = await act(() =>
      result.current.begin({
        commandType: "item.delete",
        registerId: REGISTER_ID,
        saleId: SALE_ID,
        itemId: ITEM_ID,
      }),
    );

    expect(second.applied).toBeNull();
    expect(apiMocks.deleteSaleItem).not.toHaveBeenCalled();
    expect(loadPendingPosCommand(REGISTER_ID)?.commandType).toBe("item.update");
  });

  it("keeps a 409 collision locked without offering an unsafe replay", async () => {
    apiMocks.deleteSaleItem.mockRejectedValue(collisionError);
    const { result } = setup();

    await act(() =>
      result.current.begin({
        commandType: "item.delete",
        registerId: REGISTER_ID,
        saleId: SALE_ID,
        itemId: ITEM_ID,
      }),
    );

    expect(result.current.blocked).toBe(true);
    expect(result.current.canRetry).toBe(false);
    expect(result.current.message).toMatch(/Конфликт номера операции/i);
    expect(loadPendingPosCommand(REGISTER_ID)).not.toBeNull();
  });
});
