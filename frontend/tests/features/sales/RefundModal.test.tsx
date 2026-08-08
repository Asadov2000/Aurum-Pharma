import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mutateAsync = vi.fn();
const getRefundResult = vi.fn();
const settingsResult = {
  data: { refund_reason_mode: "optional" },
  isLoading: false,
};

vi.mock("@/features/sales/queries", () => ({
  useRefundSale: () => ({
    mutateAsync: (...args: unknown[]) => mutateAsync(...args),
    isPending: false,
  }),
}));

vi.mock("@/features/sales/api", () => ({
  getRefundResult: (...args: unknown[]) => getRefundResult(...args),
}));

vi.mock("@/features/foundation/queries", () => ({
  useTenantSettingsQuery: () => settingsResult,
}));

import { RefundModal } from "@/features/sales/RefundModal";
import { type SaleDetails } from "@/features/pos/types";
import {
  createPendingRefundOperation,
  loadPendingRefundOperation,
} from "@/features/sales/refundOperation";

const SALE: SaleDetails = {
  id: "sale-1",
  tenant_id: "tenant-1",
  branch_id: "branch-1",
  register_id: "register-1",
  shift_id: "shift-1",
  sale_type: "sale",
  parent_sale_id: null,
  status: "completed",
  receipt_number: "000001",
  operation_id: null,
  is_test: false,
  total_amount: "20.00",
  currency: "TJS",
  voided_at: null,
  voided_by_sale_id: null,
  cashier_user_id: "user-1",
  created_at: "2026-07-30T10:00:00Z",
  completed_at: "2026-07-30T10:01:00Z",
  items: [
    {
      id: "item-1",
      sale_id: "sale-1",
      catalog_id: "catalog-1",
      batch_id: "batch-1",
      qty: "2.000",
      unit_price: "10.00",
      total_price: "20.00",
      currency: "TJS",
      discount_amount: "0.00",
      position: 1,
      refunded_qty: "1.000",
    },
  ],
  payments: [
    {
      id: "payment-1",
      sale_id: "sale-1",
      operation_id: null,
      payment_method: "card",
      amount: "20.00",
      currency: "TJS",
    },
  ],
};

describe("RefundModal", () => {
  beforeEach(() => {
    window.localStorage.clear();
    mutateAsync.mockReset();
    mutateAsync.mockResolvedValue({ id: "return-1" });
    getRefundResult.mockReset();
    settingsResult.data.refund_reason_mode = "optional";
  });

  it("requires external confirmation for a card refund and sends the remaining quantity", async () => {
    const onRefunded = vi.fn();
    render(
      <RefundModal
        sale={SALE}
        onClose={() => undefined}
        onRefunded={onRefunded}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /Оформить возврат/i }));
    expect(
      await screen.findByText(/Подтвердите возврат денег во внешнем/i),
    ).toBeInTheDocument();
    expect(mutateAsync).not.toHaveBeenCalled();

    fireEvent.click(
      screen.getByRole("checkbox", {
        name: /Подтверждаю, что деньги по карте возвращены/i,
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: /Оформить возврат/i }));

    await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(1));
    expect(mutateAsync).toHaveBeenCalledWith({
      parentSaleId: SALE.id,
      payload: {
        operation_id: expect.any(String),
        items: [{ sale_item_id: "item-1", qty: "1" }],
        reason: null,
        comment: null,
        external_refund_confirmed: true,
      },
    });
    expect(onRefunded).toHaveBeenCalledWith("return-1");
  });

  it("does not ask for terminal confirmation for a cash refund", async () => {
    render(
      <RefundModal
        sale={{
          ...SALE,
          payments: [{ ...SALE.payments[0]!, payment_method: "cash" }],
        }}
        onClose={() => undefined}
        onRefunded={() => undefined}
      />,
    );

    expect(
      screen.queryByRole("checkbox", { name: /Подтверждаю, что деньги/i }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Оформить возврат/i }));

    await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(1));
    expect(mutateAsync.mock.calls[0]?.[0]).toMatchObject({
      payload: { external_refund_confirmed: false },
    });
  });

  it("recovers a committed refund when the POST response is lost", async () => {
    const onRefunded = vi.fn();
    mutateAsync.mockRejectedValue(new Error("response lost"));
    getRefundResult.mockResolvedValue({ id: "return-recovered" });
    render(
      <RefundModal sale={SALE} onClose={() => undefined} onRefunded={onRefunded} />,
    );

    fireEvent.click(
      screen.getByRole("checkbox", {
        name: /Подтверждаю, что деньги по карте возвращены/i,
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: /Оформить возврат/i }));

    await waitFor(() => expect(getRefundResult).toHaveBeenCalledTimes(1));
    expect(onRefunded).toHaveBeenCalledWith("return-recovered");
    expect(loadPendingRefundOperation(SALE.id)).toBeNull();
    expect(mutateAsync).toHaveBeenCalledTimes(1);
  });

  it("restores and reconciles an unfinished refund after reopening the modal", async () => {
    const onRefunded = vi.fn();
    const operation = createPendingRefundOperation(
      SALE.id,
      [{ sale_item_id: "item-1", qty: "1" }],
      true,
    );
    if (!operation) throw new Error("refund operation was not persisted");
    getRefundResult.mockResolvedValue({ id: "return-restored" });

    render(
      <RefundModal sale={SALE} onClose={() => undefined} onRefunded={onRefunded} />,
    );

    await waitFor(() =>
      expect(getRefundResult).toHaveBeenCalledWith(operation.operationId),
    );
    expect(onRefunded).toHaveBeenCalledWith("return-restored");
    expect(mutateAsync).not.toHaveBeenCalled();
    expect(loadPendingRefundOperation(SALE.id)).toBeNull();
  });

  it("retries a temporary 404 with the exact persisted financial payload", async () => {
    const operation = createPendingRefundOperation(
      SALE.id,
      [{ sale_item_id: "item-1", qty: "0.5" }],
      true,
    );
    if (!operation) throw new Error("refund operation was not persisted");
    getRefundResult.mockRejectedValue({
      isAxiosError: true,
      response: { status: 404, data: { detail: "not found" } },
    });
    render(
      <RefundModal sale={SALE} onClose={() => undefined} onRefunded={() => undefined} />,
    );

    expect(
      await screen.findByText(/Предыдущий возврат не найден на сервере/i),
    ).toBeInTheDocument();
    expect(screen.getByDisplayValue("0.5")).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: /Оформить возврат/i }));

    await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(1));
    expect(mutateAsync).toHaveBeenCalledWith({
      parentSaleId: SALE.id,
      payload: {
        operation_id: operation.operationId,
        items: [{ sale_item_id: "item-1", qty: "0.5" }],
        reason: null,
        comment: null,
        external_refund_confirmed: true,
      },
    });
  });

  it("blocks a refund when its recovery marker cannot be persisted", async () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("storage unavailable");
    });
    render(
      <RefundModal sale={SALE} onClose={() => undefined} onRefunded={() => undefined} />,
    );

    fireEvent.click(
      screen.getByRole("checkbox", {
        name: /Подтверждаю, что деньги по карте возвращены/i,
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: /Оформить возврат/i }));

    expect(
      await screen.findByText(/Локальное хранилище недоступно.*Возврат не отправлен/i),
    ).toBeInTheDocument();
    expect(mutateAsync).not.toHaveBeenCalled();
    setItem.mockRestore();
  });

  it("blocks duplicate submission while an earlier refund result is unknown", async () => {
    const operation = createPendingRefundOperation(
      SALE.id,
      [{ sale_item_id: "item-1", qty: "1" }],
      true,
    );
    if (!operation) throw new Error("refund operation was not persisted");
    getRefundResult.mockRejectedValue(new Error("offline"));
    render(
      <RefundModal sale={SALE} onClose={() => undefined} onRefunded={() => undefined} />,
    );

    expect(
      await screen.findByText(/Не удалось проверить результат возврата.*Не повторяйте/i),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Оформить возврат/i }),
    ).toBeDisabled();
    expect(
      screen.getByRole("button", { name: /Проверить результат/i }),
    ).toBeInTheDocument();
    expect(loadPendingRefundOperation(SALE.id)).toEqual(operation);
    expect(mutateAsync).not.toHaveBeenCalled();
  });
});
