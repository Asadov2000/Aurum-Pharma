import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { type MeResponse } from "@/features/auth/types";
import { type RefundAttempt } from "@/features/sales/types";
import { useAuthStore } from "@/stores/auth";

const mutateAsync = vi.fn();
const getRefundResult = vi.fn();
const createRefundAttempt = vi.fn();
const getActiveRefundAttempt = vi.fn();
const getRefundAttempt = vi.fn();
const beginRefundAttemptReconciliation = vi.fn();
const confirmRefundAttempt = vi.fn();
const voidRefundAttempt = vi.fn();
const activeRefundAttemptResult = {
  data: null as RefundAttempt | null,
  isFetching: false,
  isError: false,
  error: null as unknown,
  refetch: vi.fn(),
};
const useActiveRefundAttemptQuery = vi.fn(
  (_parentSaleId: string, _enabled: boolean) => activeRefundAttemptResult,
);
const TEST_USER: MeResponse = {
  id: "user-1",
  email: "synthetic@example.test",
  full_name: "Synthetic cashier",
  is_developer: false,
  is_administrator: false,
  home_tenant_id: "tenant-1",
  active_tenant_id: "tenant-1",
  status: "active",
  last_login_at: null,
  level: 4,
  is_tenant_owner: false,
  branch_assignments: { "tenant-1": "branch-1" },
  permissions: ["pos.refund", "pos.refund_external_confirm"],
  platform_capabilities: [],
  support_access: null,
};
const authResult = { user: { ...TEST_USER } };
const settingsResult = {
  data: { refund_reason_mode: "optional" },
  isLoading: false,
};

vi.mock("@/features/auth/hooks", () => ({
  useAuth: () => authResult,
}));

vi.mock("@/features/sales/queries", () => ({
  useActiveRefundAttemptQuery: (parentSaleId: string, enabled: boolean) =>
    useActiveRefundAttemptQuery(parentSaleId, enabled),
  useRefundSale: () => ({
    mutateAsync: (...args: unknown[]) => mutateAsync(...args),
    isPending: false,
  }),
}));

vi.mock("@/features/sales/api", () => ({
  getRefundResult: (...args: unknown[]) => getRefundResult(...args),
  createRefundAttempt: (...args: unknown[]) => createRefundAttempt(...args),
  getActiveRefundAttempt: (...args: unknown[]) => getActiveRefundAttempt(...args),
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
  captureRefundRecoveryContext,
  createPendingRefundOperation,
  loadPendingRefundOperation,
  pendingRefundOperationKey,
  savePendingRefundAttemptId,
} from "@/features/sales/refundOperation";

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
      name: "Парацетамол 500 мг",
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

function refundedSale(operationId: string, id = "return-1"): SaleDetails {
  return {
    ...SALE,
    id,
    sale_type: "return",
    parent_sale_id: SALE.id,
    operation_id: operationId,
  };
}

function recoveryContext() {
  const context = captureRefundRecoveryContext(SALE);
  if (!context) throw new Error("synthetic refund authority unavailable");
  return context;
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

const PENDING_ATTEMPT: RefundAttempt = {
  id: "attempt-1",
  tenant_id: "tenant-1",
  parent_sale_id: "sale-1",
  register_id: "register-1",
  requested_by_user_id: "user-1",
  confirmed_by_user_id: null,
  operation_id: "11111111-1111-4111-8111-111111111111",
  items: [{ sale_item_id: "item-1", qty: "1" }],
  intent_locked: true,
  reason: null,
  comment: null,
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

let createdAttempt = PENDING_ATTEMPT;

describe("RefundModal", () => {
  beforeEach(() => {
    window.localStorage.clear();
    useAuthStore.setState({
      accessToken: "synthetic-access",
      user: { ...TEST_USER },
      hydrated: true,
    });
    mutateAsync.mockReset();
    mutateAsync.mockImplementation(async ({ payload }: { payload: { operation_id: string } }) =>
      refundedSale(payload.operation_id),
    );
    getRefundResult.mockReset();
    createdAttempt = PENDING_ATTEMPT;
    createRefundAttempt.mockReset();
    createRefundAttempt.mockImplementation(async (_parentId: string, operationId: string) => {
      createdAttempt = { ...PENDING_ATTEMPT, operation_id: operationId };
      return createdAttempt;
    });
    getActiveRefundAttempt.mockReset();
    getActiveRefundAttempt.mockResolvedValue(null);
    getRefundAttempt.mockReset();
    getRefundAttempt.mockResolvedValue(PENDING_ATTEMPT);
    beginRefundAttemptReconciliation.mockReset();
    beginRefundAttemptReconciliation.mockImplementation(async () => ({
      ...createdAttempt,
      status: "requires_reconciliation",
    }));
    confirmRefundAttempt.mockReset();
    confirmRefundAttempt.mockImplementation(async () => ({
      ...CONFIRMED_ATTEMPT,
      operation_id: createdAttempt.operation_id,
    }));
    voidRefundAttempt.mockReset();
    voidRefundAttempt.mockImplementation(async () => ({ ...createdAttempt, status: "voided" }));
    activeRefundAttemptResult.data = null;
    activeRefundAttemptResult.isFetching = false;
    activeRefundAttemptResult.isError = false;
    activeRefundAttemptResult.error = null;
    activeRefundAttemptResult.refetch.mockReset();
    useActiveRefundAttemptQuery.mockClear();
    authResult.user.permissions = ["pos.refund", "pos.refund_external_confirm"];
    settingsResult.data.refund_reason_mode = "optional";
  });

  afterEach(() => {
    useAuthStore.getState().clear();
  });

  function selectRefundItem(): void {
    fireEvent.click(screen.getByRole("checkbox", { name: "Вернуть Парацетамол 500 мг" }));
  }

  it("starts with no selected items and shows the calculated refund total", () => {
    render(<RefundModal sale={SALE} onClose={() => undefined} onRefunded={() => undefined} />);

    expect(screen.getByRole("checkbox", { name: "Вернуть Парацетамол 500 мг" })).not.toBeChecked();
    expect(screen.getByText("Предварительно: 0,00 TJS")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Зафиксировать сумму возврата" })).toBeDisabled();

    selectRefundItem();

    expect(screen.getByText("Предварительно: 10,00 TJS")).toBeInTheDocument();
    expect(screen.getByLabelText("Количество для возврата: Парацетамол 500 мг")).toBeEnabled();
  });

  it("blocks financial actions while checking for a server-side active attempt", () => {
    activeRefundAttemptResult.isFetching = true;

    render(<RefundModal sale={SALE} onClose={() => undefined} onRefunded={() => undefined} />);

    expect(screen.getByText(/Проверяем, нет ли незавершённого/i)).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Вернуть Парацетамол 500 мг" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Зафиксировать сумму возврата" })).toBeDisabled();
  });

  it("restores a server-side pending attempt without starting reconciliation", async () => {
    activeRefundAttemptResult.data = PENDING_ATTEMPT;

    render(<RefundModal sale={SALE} onClose={() => undefined} onRefunded={() => undefined} />);

    expect(await screen.findByText(/Деньги ещё не возвращались/i)).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Вернуть Парацетамол 500 мг" })).toBeChecked();
    expect(screen.getByRole("button", { name: "Отменить заявку" })).toBeEnabled();
    expect(beginRefundAttemptReconciliation).not.toHaveBeenCalled();
    expect(loadPendingRefundOperation(SALE.id, recoveryContext())).toMatchObject({
      refundAttemptOperationId: PENDING_ATTEMPT.operation_id,
      refundAttemptId: PENDING_ATTEMPT.id,
      parentSaleId: SALE.id,
      items: PENDING_ATTEMPT.items,
    });
  });

  it("restores a server-side attempt that requires reconciliation", async () => {
    activeRefundAttemptResult.data = {
      ...PENDING_ATTEMPT,
      status: "requires_reconciliation",
    };

    render(<RefundModal sale={SALE} onClose={() => undefined} onRefunded={() => undefined} />);

    expect(
      await screen.findByText(/Не повторяйте возврат во внешнем терминале/i),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Терминал")).toBeEnabled();
    expect(beginRefundAttemptReconciliation).not.toHaveBeenCalled();
  });

  it("restores a confirmed server-side attempt and only creates the return receipt", async () => {
    activeRefundAttemptResult.data = CONFIRMED_ATTEMPT;

    render(<RefundModal sale={SALE} onClose={() => undefined} onRefunded={() => undefined} />);

    expect(await screen.findByText(/Осталось создать чек возврата/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Вернуть выбранное/ }));

    await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(1));
    expect(createRefundAttempt).not.toHaveBeenCalled();
    expect(beginRefundAttemptReconciliation).not.toHaveBeenCalled();
    expect(confirmRefundAttempt).not.toHaveBeenCalled();
    expect(mutateAsync.mock.calls[0]?.[0]).toMatchObject({
      parentSaleId: SALE.id,
      payload: {
        refund_attempt_id: CONFIRMED_ATTEMPT.id,
        items: CONFIRMED_ATTEMPT.items,
      },
    });
  });

  it("keeps the ordinary flow when the server has no active attempt", async () => {
    activeRefundAttemptResult.data = null;

    render(<RefundModal sale={SALE} onClose={() => undefined} onRefunded={() => undefined} />);

    await waitFor(() => expect(useActiveRefundAttemptQuery).toHaveBeenCalledWith(SALE.id, true));
    expect(loadPendingRefundOperation(SALE.id, recoveryContext())).toBeNull();
    expect(screen.getByRole("checkbox", { name: "Вернуть Парацетамол 500 мг" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "Отменить заявку" })).not.toBeInTheDocument();
  });

  it("fails closed when the active-attempt lookup fails", async () => {
    activeRefundAttemptResult.isError = true;
    activeRefundAttemptResult.error = new Error("network unavailable");

    render(<RefundModal sale={SALE} onClose={() => undefined} onRefunded={() => undefined} />);

    expect(await screen.findByText(/Не удалось проверить незавершённый/i)).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Вернуть Парацетамол 500 мг" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Повторить поиск" })).toBeEnabled();
    expect(mutateAsync).not.toHaveBeenCalled();
  });

  it("fails closed when a server-side attempt cannot be persisted locally", async () => {
    activeRefundAttemptResult.data = PENDING_ATTEMPT;
    const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("storage unavailable");
    });

    render(<RefundModal sale={SALE} onClose={() => undefined} onRefunded={() => undefined} />);

    expect(
      await screen.findByText(/Не удалось безопасно сохранить найденную заявку/i),
    ).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Вернуть Парацетамол 500 мг" })).toBeDisabled();
    expect(beginRefundAttemptReconciliation).not.toHaveBeenCalled();
    expect(mutateAsync).not.toHaveBeenCalled();
    setItem.mockRestore();
  });

  it("binds a card refund to terminal document details before posting the return", async () => {
    const onRefunded = vi.fn();
    const boundAttempt = { ...PENDING_ATTEMPT, reason: "quality_issue" as const };
    const reconcilingAttempt = { ...boundAttempt, status: "requires_reconciliation" as const };
    const confirmedAttempt = {
      ...CONFIRMED_ATTEMPT,
      reason: "quality_issue" as const,
    };
    createRefundAttempt.mockImplementationOnce(async (_parentId: string, operationId: string) => {
      createdAttempt = { ...boundAttempt, operation_id: operationId };
      return createdAttempt;
    });
    beginRefundAttemptReconciliation.mockImplementationOnce(async () => ({
      ...reconcilingAttempt,
      operation_id: createdAttempt.operation_id,
    }));
    confirmRefundAttempt.mockImplementationOnce(async () => ({
      ...confirmedAttempt,
      operation_id: createdAttempt.operation_id,
    }));
    render(<RefundModal sale={SALE} onClose={() => undefined} onRefunded={onRefunded} />);

    expect(screen.getByRole("note")).toHaveTextContent("Возвращённый товар не поступит в продажу");
    expect(screen.getByRole("option", { name: "Подозрение на качество" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Причина"), {
      target: { value: "quality_issue" },
    });

    selectRefundItem();
    fireEvent.click(screen.getByRole("button", { name: "Зафиксировать сумму возврата" }));
    await waitFor(() => expect(createRefundAttempt).toHaveBeenCalledTimes(1));
    expect(createRefundAttempt).toHaveBeenCalledWith(
      SALE.id,
      expect.any(String),
      [{ sale_item_id: "item-1", qty: "1" }],
      "quality_issue",
      null,
    );
    expect(beginRefundAttemptReconciliation).toHaveBeenCalledWith("attempt-1");
    expect(mutateAsync).not.toHaveBeenCalled();
    expect(screen.getByText("К возврату: 10,00 TJS")).toBeInTheDocument();
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
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить возврат и создать чек" }));

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
        reason: "quality_issue",
        comment: null,
        refund_attempt_id: "attempt-1",
      },
    });
    expect(onRefunded).toHaveBeenCalledWith("return-1");
    expect(loadPendingRefundOperation(SALE.id, recoveryContext())).toBeNull();
  });

  it("lets a cashier create a request but hides confirmation controls without permission", async () => {
    authResult.user.permissions = ["pos.refund"];
    render(<RefundModal sale={SALE} onClose={() => undefined} onRefunded={() => undefined} />);

    selectRefundItem();
    fireEvent.click(screen.getByRole("button", { name: "Зафиксировать сумму возврата" }));

    expect(await screen.findByText(/Для подтверждения пригласите сотрудника/i)).toBeInTheDocument();
    expect(screen.getByLabelText("Терминал")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Ожидает подтверждения" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Отменить заявку" })).not.toBeInTheDocument();
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

    selectRefundItem();
    fireEvent.click(screen.getByRole("button", { name: /Вернуть выбранное/ }));

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
      recoveryContext(),
    );
    if (!operation) throw new Error("refund operation was not persisted");
    const updated = savePendingRefundAttemptId(operation, PENDING_ATTEMPT.id, recoveryContext());
    if (!updated) throw new Error("refund attempt was not persisted");
    getRefundResult.mockRejectedValue({
      isAxiosError: true,
      response: { status: 404, data: { detail: "not found" } },
    });
    getRefundAttempt.mockResolvedValue({
      ...CONFIRMED_ATTEMPT,
      operation_id: operation.refundAttemptOperationId,
    });
    render(<RefundModal sale={SALE} onClose={() => undefined} onRefunded={() => undefined} />);

    expect(await screen.findByText(/Подтверждение восстановлено/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Вернуть выбранное/ }));

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
    getRefundResult.mockImplementation(async (operationId: string) =>
      refundedSale(operationId, "return-recovered"),
    );
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

    selectRefundItem();
    fireEvent.click(screen.getByRole("button", { name: /Вернуть выбранное/ }));

    await waitFor(() => expect(getRefundResult).toHaveBeenCalledTimes(1));
    expect(onRefunded).toHaveBeenCalledWith("return-recovered");
    expect(loadPendingRefundOperation(SALE.id, recoveryContext())).toBeNull();
  });

  it("recovers an electronic refund after the final response is lost without repeating confirmation", async () => {
    const onRefunded = vi.fn();
    mutateAsync.mockRejectedValue(new Error("response lost"));
    getRefundResult.mockImplementation(async (operationId: string) =>
      refundedSale(operationId, "return-electronic-recovered"),
    );
    render(<RefundModal sale={SALE} onClose={() => undefined} onRefunded={onRefunded} />);

    selectRefundItem();
    fireEvent.click(screen.getByRole("button", { name: "Зафиксировать сумму возврата" }));
    await screen.findByLabelText("Терминал");
    fireEvent.change(screen.getByLabelText("Терминал"), { target: { value: "TERM-01" } });
    fireEvent.change(screen.getByLabelText("Номер документа"), {
      target: { value: "REFUND-LOST-RESPONSE" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить возврат и создать чек" }));

    await waitFor(() => expect(getRefundResult).toHaveBeenCalledTimes(1));
    expect(confirmRefundAttempt).toHaveBeenCalledTimes(1);
    expect(mutateAsync).toHaveBeenCalledTimes(1);
    expect(onRefunded).toHaveBeenCalledWith("return-electronic-recovered");
    expect(loadPendingRefundOperation(SALE.id, recoveryContext())).toBeNull();
  });

  it("cancels a pending request without posting a return", async () => {
    render(<RefundModal sale={SALE} onClose={() => undefined} onRefunded={() => undefined} />);
    selectRefundItem();
    fireEvent.click(screen.getByRole("button", { name: "Зафиксировать сумму возврата" }));
    await screen.findByRole("button", { name: "Отменить заявку" });

    fireEvent.click(screen.getByRole("button", { name: "Отменить заявку" }));
    fireEvent.click(screen.getByRole("button", { name: "Деньги не возвращены — отменить" }));

    await waitFor(() => expect(voidRefundAttempt).toHaveBeenCalledWith("attempt-1"));
    expect(mutateAsync).not.toHaveBeenCalled();
    expect(loadPendingRefundOperation(SALE.id, recoveryContext())).toBeNull();
  });

  it("blocks submission when the recovery marker cannot be persisted", async () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("storage unavailable");
    });
    render(<RefundModal sale={SALE} onClose={() => undefined} onRefunded={() => undefined} />);

    selectRefundItem();
    fireEvent.click(screen.getByRole("button", { name: "Зафиксировать сумму возврата" }));

    expect(
      await screen.findByText(/Локальное хранилище недоступно.*Возврат не отправлен/i),
    ).toBeInTheDocument();
    expect(createRefundAttempt).not.toHaveBeenCalled();
    setItem.mockRestore();
  });

  const authorityChanges = ["logout", "different user", "different tenant"] as const;
  function changeAuthority(change: (typeof authorityChanges)[number]): void {
    if (change === "logout") {
      useAuthStore.getState().clear();
    } else {
      useAuthStore.getState().setUser({
        ...TEST_USER,
        ...(change === "different user" ? { id: "user-2" } : { active_tenant_id: "tenant-2" }),
      });
    }
  }

  it("does not automatically adopt another requester's active refund", async () => {
    activeRefundAttemptResult.data = { ...PENDING_ATTEMPT, requested_by_user_id: "user-2" };
    render(<RefundModal sale={SALE} onClose={() => undefined} onRefunded={vi.fn()} />);

    await act(async () => undefined);
    expect(loadPendingRefundOperation(SALE.id, recoveryContext())).toBeNull();
    expect(createRefundAttempt).not.toHaveBeenCalled();
    expect(beginRefundAttemptReconciliation).not.toHaveBeenCalled();
    expect(confirmRefundAttempt).not.toHaveBeenCalled();
    expect(mutateAsync).not.toHaveBeenCalled();
    expect(screen.getByRole("checkbox", { name: "Вернуть Парацетамол 500 мг" })).toBeDisabled();
  });

  it("retains an unresolved request and blocks a new refund when active lookup returns null", async () => {
    const context = recoveryContext();
    const operation = createPendingRefundOperation(
      SALE.id,
      [{ sale_item_id: "item-1", qty: "1" }],
      true,
      context,
    );
    if (!operation) throw new Error("synthetic operation unavailable");
    const key = pendingRefundOperationKey(SALE.id, context);
    const original = window.localStorage.getItem(key);
    getRefundResult.mockRejectedValue({ isAxiosError: true, response: { status: 404 } });
    render(<RefundModal sale={SALE} onClose={() => undefined} onRefunded={vi.fn()} />);

    await waitFor(() => expect(getActiveRefundAttempt).toHaveBeenCalledTimes(1));
    await act(async () => undefined);
    expect(window.localStorage.getItem(key)).toBe(original);
    expect(loadPendingRefundOperation(SALE.id, context)).toEqual(operation);
    expect(screen.getByRole("button", { name: "Зафиксировать сумму возврата" })).toBeDisabled();
    expect(createRefundAttempt).not.toHaveBeenCalled();
    expect(mutateAsync).not.toHaveBeenCalled();
  });

  describe.each(authorityChanges)("pending refund after %s", (change) => {
    it.each(["success", "failure"] as const)(
      "ignores a late create response: %s",
      async (outcome) => {
        const response = deferred<RefundAttempt>();
        createRefundAttempt.mockReturnValueOnce(response.promise);
        const onRefunded = vi.fn();
        const context = recoveryContext();
        const key = pendingRefundOperationKey(SALE.id, context);
        render(<RefundModal sale={SALE} onClose={() => undefined} onRefunded={onRefunded} />);
        selectRefundItem();
        fireEvent.click(screen.getByRole("button", { name: "Зафиксировать сумму возврата" }));
        await waitFor(() => expect(createRefundAttempt).toHaveBeenCalledTimes(1));
        const original = window.localStorage.getItem(key);
        expect(original).not.toBeNull();

        await act(async () => {
          changeAuthority(change);
          if (outcome === "success") response.resolve(PENDING_ATTEMPT);
          else response.reject(new Error("late network failure"));
        });

        expect(window.localStorage.getItem(key)).toBe(original);
        expect(getRefundResult).not.toHaveBeenCalled();
        expect(getActiveRefundAttempt).not.toHaveBeenCalled();
        expect(getRefundAttempt).not.toHaveBeenCalled();
        expect(beginRefundAttemptReconciliation).not.toHaveBeenCalled();
        expect(confirmRefundAttempt).not.toHaveBeenCalled();
        expect(mutateAsync).not.toHaveBeenCalled();
        expect(onRefunded).not.toHaveBeenCalled();
      },
    );

    it.each(["success", "failure"] as const)(
      "preserves the financial operation after a late final POST: %s",
      async (outcome) => {
        const response = deferred<{ id: string }>();
        mutateAsync.mockReturnValueOnce(response.promise);
        const onRefunded = vi.fn();
        const context = recoveryContext();
        const key = pendingRefundOperationKey(SALE.id, context);
        render(
          <RefundModal
            sale={{ ...SALE, payments: [{ ...SALE.payments[0]!, payment_method: "cash" }] }}
            onClose={() => undefined}
            onRefunded={onRefunded}
          />,
        );
        selectRefundItem();
        fireEvent.click(screen.getByRole("button", { name: /Вернуть выбранное/ }));
        await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(1));
        const original = window.localStorage.getItem(key);
        expect(original).not.toBeNull();

        await act(async () => {
          changeAuthority(change);
          if (outcome === "success") response.resolve({ id: "late-return" });
          else response.reject(new Error("late response lost"));
        });

        expect(window.localStorage.getItem(key)).toBe(original);
        expect(getRefundResult).not.toHaveBeenCalled();
        expect(getActiveRefundAttempt).not.toHaveBeenCalled();
        expect(createRefundAttempt).not.toHaveBeenCalled();
        expect(confirmRefundAttempt).not.toHaveBeenCalled();
        expect(mutateAsync).toHaveBeenCalledTimes(1);
        expect(onRefunded).not.toHaveBeenCalled();
      },
    );

    it.each(["found", "missing"] as const)(
      "ignores a late recovery lookup: %s",
      async (outcome) => {
        const response = deferred<{ id: string }>();
        getRefundResult.mockReturnValueOnce(response.promise);
        const onRefunded = vi.fn();
        const context = recoveryContext();
        const operation = createPendingRefundOperation(
          SALE.id,
          [{ sale_item_id: "item-1", qty: "1" }],
          true,
          context,
        );
        if (!operation) throw new Error("synthetic operation unavailable");
        const key = pendingRefundOperationKey(SALE.id, context);
        const original = window.localStorage.getItem(key);
        render(<RefundModal sale={SALE} onClose={() => undefined} onRefunded={onRefunded} />);
        await waitFor(() => expect(getRefundResult).toHaveBeenCalledTimes(1));

        await act(async () => {
          changeAuthority(change);
          if (outcome === "found") response.resolve({ id: "late-recovered-return" });
          else response.reject({ isAxiosError: true, response: { status: 404 } });
        });

        expect(window.localStorage.getItem(key)).toBe(original);
        expect(getActiveRefundAttempt).not.toHaveBeenCalled();
        expect(getRefundAttempt).not.toHaveBeenCalled();
        expect(beginRefundAttemptReconciliation).not.toHaveBeenCalled();
        expect(mutateAsync).not.toHaveBeenCalled();
        expect(onRefunded).not.toHaveBeenCalled();
      },
    );
  });

  it("preserves a pending financial command when its modal unmounts before success", async () => {
    const response = deferred<{ id: string }>();
    mutateAsync.mockReturnValueOnce(response.promise);
    const onRefunded = vi.fn();
    const context = recoveryContext();
    const key = pendingRefundOperationKey(SALE.id, context);
    const view = render(
      <RefundModal
        sale={{ ...SALE, payments: [{ ...SALE.payments[0]!, payment_method: "cash" }] }}
        onClose={() => undefined}
        onRefunded={onRefunded}
      />,
    );
    selectRefundItem();
    fireEvent.click(screen.getByRole("button", { name: /Вернуть выбранное/ }));
    await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(1));
    const original = window.localStorage.getItem(key);
    expect(original).not.toBeNull();
    view.unmount();
    await act(async () => response.resolve({ id: "late-return" }));

    expect(window.localStorage.getItem(key)).toBe(original);
    expect(getRefundResult).not.toHaveBeenCalled();
    expect(onRefunded).not.toHaveBeenCalled();
  });

  it("finishes the same user's command after access token rotation", async () => {
    const response = deferred<SaleDetails>();
    mutateAsync.mockReturnValueOnce(response.promise);
    const onRefunded = vi.fn();
    const context = recoveryContext();
    render(
      <RefundModal
        sale={{ ...SALE, payments: [{ ...SALE.payments[0]!, payment_method: "cash" }] }}
        onClose={() => undefined}
        onRefunded={onRefunded}
      />,
    );
    selectRefundItem();
    fireEvent.click(screen.getByRole("button", { name: /Вернуть выбранное/ }));
    await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(1));
    const operation = loadPendingRefundOperation(SALE.id, context);
    if (!operation) throw new Error("synthetic pending command unavailable");
    await act(async () => {
      useAuthStore.setState({ accessToken: "synthetic-rotated-access" });
      response.resolve(refundedSale(operation.operationId, "return-after-rotation"));
    });

    expect(onRefunded).toHaveBeenCalledWith("return-after-rotation");
    expect(loadPendingRefundOperation(SALE.id, context)).toBeNull();
    expect(getRefundResult).not.toHaveBeenCalled();
  });

  it.each([
    { tenant_id: "tenant-2" },
    { branch_id: "branch-2" },
    { register_id: "register-2" },
    { parent_sale_id: "another-sale" },
    { operation_id: "22222222-2222-4222-8222-222222222222" },
    { sale_type: "sale" as const },
    { status: "draft" as const },
  ])("retains the original command when recovery returns unrelated data: %j", async (mismatch) => {
    const context = recoveryContext();
    const operation = createPendingRefundOperation(
      SALE.id,
      [{ sale_item_id: "item-1", qty: "1" }],
      false,
      context,
    );
    if (!operation) throw new Error("synthetic pending command unavailable");
    const key = pendingRefundOperationKey(SALE.id, context);
    const original = window.localStorage.getItem(key);
    getRefundResult.mockResolvedValue({ ...refundedSale(operation.operationId), ...mismatch });
    const onRefunded = vi.fn();
    render(
      <RefundModal
        sale={{ ...SALE, payments: [{ ...SALE.payments[0]!, payment_method: "cash" }] }}
        onClose={() => undefined}
        onRefunded={onRefunded}
      />,
    );

    await waitFor(() => expect(getRefundResult).toHaveBeenCalledTimes(1));
    await act(async () => undefined);
    expect(window.localStorage.getItem(key)).toBe(original);
    expect(onRefunded).not.toHaveBeenCalled();
    expect(mutateAsync).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /Вернуть выбранное/ })).toBeDisabled();
  });

  it.each([401, 403, 500])(
    "preserves the cash refund command after POST %s and a missing result",
    async (status) => {
      mutateAsync.mockRejectedValue({ isAxiosError: true, response: { status } });
      getRefundResult.mockRejectedValue({ isAxiosError: true, response: { status: 404 } });
      const context = recoveryContext();
      const key = pendingRefundOperationKey(SALE.id, context);
      const onRefunded = vi.fn();
      render(
        <RefundModal
          sale={{ ...SALE, payments: [{ ...SALE.payments[0]!, payment_method: "cash" }] }}
          onClose={() => undefined}
          onRefunded={onRefunded}
        />,
      );
      selectRefundItem();
      fireEvent.click(screen.getByRole("button", { name: /Вернуть выбранное/ }));
      await waitFor(() => expect(getRefundResult).toHaveBeenCalledTimes(1));
      await act(async () => undefined);

      const command = loadPendingRefundOperation(SALE.id, context);
      expect(window.localStorage.getItem(key)).not.toBeNull();
      expect(command).toMatchObject({
        operationId: mutateAsync.mock.calls[0]?.[0]?.payload.operation_id,
        refundAttemptOperationId: null,
        refundAttemptId: null,
      });
      expect(getRefundResult).toHaveBeenCalledWith(command?.operationId);
      expect(mutateAsync).toHaveBeenCalledTimes(1);
      expect(createRefundAttempt).not.toHaveBeenCalled();
      expect(onRefunded).not.toHaveBeenCalled();
      expect(screen.getByRole("button", { name: /Вернуть выбранное/ })).toBeDisabled();
    },
  );
});
