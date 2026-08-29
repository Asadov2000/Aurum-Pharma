import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mutateAsync = vi.fn();
const getRefundResult = vi.fn();
const createRefundAttempt = vi.fn();
const getRefundAttempt = vi.fn();
const beginRefundAttemptReconciliation = vi.fn();
const confirmRefundAttempt = vi.fn();
const voidRefundAttempt = vi.fn();
const authResult = {
  user: {
    is_developer: false,
    permissions: ["pos.refund", "pos.refund_external_confirm"],
  },
};
const settingsResult = {
  data: { refund_reason_mode: "optional" },
  isLoading: false,
};

vi.mock("@/features/auth/hooks", () => ({
  useAuth: () => authResult,
}));

vi.mock("@/features/sales/queries", () => ({
  useRefundSale: () => ({
    mutateAsync: (...args: unknown[]) => mutateAsync(...args),
    isPending: false,
  }),
}));

vi.mock("@/features/sales/api", () => ({
  getRefundResult: (...args: unknown[]) => getRefundResult(...args),
  createRefundAttempt: (...args: unknown[]) => createRefundAttempt(...args),
  getRefundAttempt: (...args: unknown[]) => getRefundAttempt(...args),
  beginRefundAttemptReconciliation: (...args: unknown[]) =>
    beginRefundAttemptReconciliation(...args),
  confirmRefundAttempt: (...args: unknown[]) => confirmRefundAttempt(...args),
  voidRefundAttempt: (...args: unknown[]) => voidRefundAttempt(...args),
}));

vi.mock("@/features/foundation/queries", () => ({
  useTenantOperationalSettingsQuery: () => settingsResult,
}));

import { type SaleDetails } from "@/features/pos/types";
import { RefundModal } from "@/features/sales/RefundModal";
import {
  createPendingRefundOperation,
  loadPendingRefundOperation,
  savePendingRefundAttemptId,
} from "@/features/sales/refundOperation";
import { type RefundAttempt } from "@/features/sales/types";

const SALE: SaleDetails = {
  id: "sale-1",
  tenant_id: "tenant-1",
  branch_id: "branch-1",
  register_id: "register-1",
  shift_id: "shift-1",
  sale_type: "sale",
  parent_sale_id: null,
  refund_attempt_id: null,
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

const PENDING_ATTEMPT: RefundAttempt = {
  id: "attempt-1",
  tenant_id: "tenant-1",
  parent_sale_id: "sale-1",
  register_id: "register-1",
  requested_by_user_id: "user-1",
  confirmed_by_user_id: null,
  operation_id: "11111111-1111-4111-8111-111111111111",
  items: [{ sale_item_id: "item-1", qty: "1" }],
  payments: [
    {
      payment_method: "card",
      amount: "10.00",
      terminal_id: null,
      document_number: null,
      confirmed_by_user_id: null,
      confirmed_at: null,
    },
  ],
  total_amount: "10.00",
  external_amount: "10.00",
  currency: "TJS",
  status: "pending",
  void_reason: null,
  void_note: null,
  created_at: "2026-08-09T10:00:00Z",
  confirmed_at: null,
  consumed_at: null,
  voided_at: null,
};

const CONFIRMED_ATTEMPT: RefundAttempt = {
  ...PENDING_ATTEMPT,
  confirmed_by_user_id: "manager-1",
  status: "confirmed",
  confirmed_at: "2026-08-09T10:01:00Z",
  payments: [
    {
      ...PENDING_ATTEMPT.payments[0]!,
      terminal_id: "TERM-01",
      document_number: "REFUND-001",
      confirmed_by_user_id: "manager-1",
      confirmed_at: "2026-08-09T10:01:00Z",
    },
  ],
};

describe("RefundModal", () => {
  beforeEach(() => {
    window.localStorage.clear();
    mutateAsync.mockReset();
    mutateAsync.mockResolvedValue({ id: "return-1" });
    getRefundResult.mockReset();
    createRefundAttempt.mockReset();
    createRefundAttempt.mockResolvedValue(PENDING_ATTEMPT);
    getRefundAttempt.mockReset();
    getRefundAttempt.mockResolvedValue(PENDING_ATTEMPT);
    beginRefundAttemptReconciliation.mockReset();
    beginRefundAttemptReconciliation.mockResolvedValue({
      ...PENDING_ATTEMPT,
      status: "requires_reconciliation",
    });
    confirmRefundAttempt.mockReset();
    confirmRefundAttempt.mockResolvedValue(CONFIRMED_ATTEMPT);
    voidRefundAttempt.mockReset();
    voidRefundAttempt.mockResolvedValue({ ...PENDING_ATTEMPT, status: "voided" });
    authResult.user.permissions = ["pos.refund", "pos.refund_external_confirm"];
    settingsResult.data.refund_reason_mode = "optional";
  });

  it("binds a card refund to terminal document details before posting the return", async () => {
    const onRefunded = vi.fn();
    render(<RefundModal sale={SALE} onClose={() => undefined} onRefunded={onRefunded} />);

    fireEvent.click(screen.getByRole("button", { name: "Рассчитать возврат" }));
    await waitFor(() => expect(createRefundAttempt).toHaveBeenCalledTimes(1));
    expect(beginRefundAttemptReconciliation).toHaveBeenCalledWith("attempt-1");
    expect(mutateAsync).not.toHaveBeenCalled();
    expect(await screen.findByText("Карта")).toBeInTheDocument();
    expect(screen.getAllByText("10.00 TJS")).toHaveLength(2);
    expect(screen.getByLabelText("Терминал")).toBeEnabled();
    expect(screen.getByLabelText("Номер документа")).toBeEnabled();

    fireEvent.change(screen.getByLabelText("Терминал"), {
      target: { value: " TERM-01 " },
    });
    fireEvent.change(screen.getByLabelText("Номер документа"), {
      target: { value: " REFUND-001 " },
    });
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить и оформить" }));

    await waitFor(() => expect(confirmRefundAttempt).toHaveBeenCalledTimes(1));
    expect(confirmRefundAttempt).toHaveBeenCalledWith("attempt-1", [
      {
        payment_method: "card",
        terminal_id: "TERM-01",
        document_number: "REFUND-001",
      },
    ]);
    await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(1));
    expect(mutateAsync).toHaveBeenCalledWith({
      parentSaleId: SALE.id,
      payload: {
        operation_id: expect.any(String),
        items: [{ sale_item_id: "item-1", qty: "1" }],
        reason: null,
        comment: null,
        refund_attempt_id: "attempt-1",
      },
    });
    expect(onRefunded).toHaveBeenCalledWith("return-1");
    expect(loadPendingRefundOperation(SALE.id)).toBeNull();
  });

  it("lets a cashier create a request but hides confirmation controls without permission", async () => {
    authResult.user.permissions = ["pos.refund"];
    render(<RefundModal sale={SALE} onClose={() => undefined} onRefunded={() => undefined} />);

    fireEvent.click(screen.getByRole("button", { name: "Рассчитать возврат" }));

    expect(await screen.findByText(/Для подтверждения пригласите сотрудника/i)).toBeInTheDocument();
    expect(screen.getByLabelText("Терминал")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Ожидает подтверждения" })).toBeDisabled();
    expect(
      screen.queryByRole("button", { name: "Терминал проверен, возврата нет" }),
    ).not.toBeInTheDocument();
    expect(confirmRefundAttempt).not.toHaveBeenCalled();
  });

  it("posts a cash refund directly without an external attempt", async () => {
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

    fireEvent.click(screen.getByRole("button", { name: "Оформить возврат" }));

    await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(1));
    expect(createRefundAttempt).not.toHaveBeenCalled();
    expect(mutateAsync.mock.calls[0]?.[0]).toMatchObject({
      payload: { refund_attempt_id: null },
    });
  });

  it("restores a confirmed attempt and retries the same financial operation", async () => {
    const operation = createPendingRefundOperation(
      SALE.id,
      [{ sale_item_id: "item-1", qty: "1" }],
      true,
    );
    if (!operation) throw new Error("refund operation was not persisted");
    const updated = savePendingRefundAttemptId(operation, PENDING_ATTEMPT.id);
    if (!updated) throw new Error("refund attempt was not persisted");
    getRefundResult.mockRejectedValue({
      isAxiosError: true,
      response: { status: 404, data: { detail: "not found" } },
    });
    getRefundAttempt.mockResolvedValue(CONFIRMED_ATTEMPT);
    render(<RefundModal sale={SALE} onClose={() => undefined} onRefunded={() => undefined} />);

    expect(await screen.findByText(/Подтверждение восстановлено/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Оформить возврат" }));

    await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(1));
    expect(mutateAsync.mock.calls[0]?.[0]).toMatchObject({
      payload: {
        operation_id: operation.operationId,
        refund_attempt_id: PENDING_ATTEMPT.id,
      },
    });
  });

  it("recovers a committed refund when the final POST response is lost", async () => {
    const onRefunded = vi.fn();
    mutateAsync.mockRejectedValue(new Error("response lost"));
    getRefundResult.mockResolvedValue({ id: "return-recovered" });
    render(
      <RefundModal
        sale={{
          ...SALE,
          payments: [{ ...SALE.payments[0]!, payment_method: "cash" }],
        }}
        onClose={() => undefined}
        onRefunded={onRefunded}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Оформить возврат" }));

    await waitFor(() => expect(getRefundResult).toHaveBeenCalledTimes(1));
    expect(onRefunded).toHaveBeenCalledWith("return-recovered");
    expect(loadPendingRefundOperation(SALE.id)).toBeNull();
  });

  it("cancels a pending request without posting a return", async () => {
    render(<RefundModal sale={SALE} onClose={() => undefined} onRefunded={() => undefined} />);
    fireEvent.click(screen.getByRole("button", { name: "Рассчитать возврат" }));
    await screen.findByRole("button", { name: "Терминал проверен, возврата нет" });

    fireEvent.click(screen.getByRole("button", { name: "Терминал проверен, возврата нет" }));

    await waitFor(() => expect(voidRefundAttempt).toHaveBeenCalledWith("attempt-1"));
    expect(mutateAsync).not.toHaveBeenCalled();
    expect(loadPendingRefundOperation(SALE.id)).toBeNull();
  });

  it("blocks submission when the recovery marker cannot be persisted", async () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("storage unavailable");
    });
    render(<RefundModal sale={SALE} onClose={() => undefined} onRefunded={() => undefined} />);

    fireEvent.click(screen.getByRole("button", { name: "Рассчитать возврат" }));

    expect(
      await screen.findByText(/Локальное хранилище недоступно.*Возврат не отправлен/i),
    ).toBeInTheDocument();
    expect(createRefundAttempt).not.toHaveBeenCalled();
    setItem.mockRestore();
  });
});
