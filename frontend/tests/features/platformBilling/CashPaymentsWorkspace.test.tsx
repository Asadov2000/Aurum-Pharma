import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const cashApi = vi.hoisted(() => ({
  listTenantCashPayments: vi.fn(),
  getTenantCashReceipt: vi.fn(),
  listPlatformCashPayments: vi.fn(),
  getPlatformCashReceipt: vi.fn(),
  createPlatformCashPayment: vi.fn(),
  approvePlatformCashPayment: vi.fn(),
  rejectPlatformCashPayment: vi.fn(),
}));

vi.mock("@/features/billing/cashApi", () => cashApi);

import { type CashPayment } from "@/features/billing/cashTypes";
import { CashPaymentsWorkspace } from "@/features/platformBilling/CashPaymentsWorkspace";
import { type PlatformFinancialInvoice } from "@/features/platformBilling/types";

const TENANT_ID = "22222222-2222-4222-8222-222222222222";
const OPERATION_ID = "44444444-4444-4444-8444-444444444444";
const PENDING: CashPayment = {
  id: "11111111-1111-4111-8111-111111111111",
  tenant_id: TENANT_ID,
  document_number: "AC-2026-0042",
  status: "pending_approval",
  row_version: 1,
  amount: "600.00",
  currency: "TJS",
  paid_at: "2026-09-26T08:00:00Z",
  purpose: "Аванс за обслуживание Aurum Pharma",
  target_invoice_id: null,
  created_by: "33333333-3333-4333-8333-333333333333",
  created_at: "2026-09-26T08:01:00Z",
  confirmed_at: null,
  allocated_amount: "0.00",
  credit_amount: "0.00",
  can_approve: true,
  can_adjust: false,
};
const CONFIRMED: CashPayment = {
  ...PENDING,
  status: "confirmed",
  row_version: 2,
  confirmed_at: "2026-09-26T08:15:00Z",
  credit_amount: "600.00",
  can_approve: false,
};
const INVOICE: PlatformFinancialInvoice = {
  invoice_id: "55555555-5555-4555-8555-555555555555",
  tenant_id: TENANT_ID,
  subscription_id: "66666666-6666-4666-8666-666666666666",
  price_application_id: "77777777-7777-4777-8777-777777777777",
  price_application_kind: "initial",
  invoice_number: "AF-2026-0042",
  document_state: "issued",
  settlement_state: "unpaid",
  collection_state: "not_due",
  period_start: "2026-09-01T00:00:00Z",
  period_end: "2026-10-01T00:00:00Z",
  due_at: "2026-09-30T00:00:00Z",
  total_amount: "250.00",
  outstanding_amount: "250.00",
  currency: "TJS",
  issued_at: "2026-09-01T00:00:00Z",
};

function renderWorkspace({
  canReview = true,
  canApprove = true,
  online = true,
  invoices = [] as readonly PlatformFinancialInvoice[],
} = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <CashPaymentsWorkspace
        tenantId={TENANT_ID}
        invoices={invoices}
        canReview={canReview}
        canApprove={canApprove}
        online={online}
        refreshSignal={0}
      />
    </QueryClientProvider>,
  );
}

async function openRegistration() {
  fireEvent.click(await screen.findByRole("button", { name: "Зарегистрировать наличные" }));
  return screen.getByRole("dialog", { name: "Регистрация наличной оплаты" });
}

function fillRegistration(dialog: HTMLElement) {
  fireEvent.change(within(dialog).getByLabelText("Сумма, TJS"), { target: { value: "600,00" } });
  fireEvent.change(within(dialog).getByLabelText("Дата и время получения"), {
    target: { value: "2026-09-25T10:00" },
  });
  fireEvent.change(within(dialog).getByLabelText("Назначение оплаты"), {
    target: { value: PENDING.purpose },
  });
}

describe("platform cash payment workflow", () => {
  beforeEach(() => {
    Object.values(cashApi).forEach((mock) => mock.mockReset());
    cashApi.listPlatformCashPayments.mockResolvedValue({
      items: [PENDING],
      total: 1,
      limit: 20,
      offset: 0,
    });
    vi.spyOn(crypto, "randomUUID").mockReturnValue(OPERATION_ID);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("keeps pending money out of confirmed balance and does not offer its receipt", async () => {
    renderWorkspace({ canReview: false, canApprove: false });

    expect(await screen.findByText("На подтверждении")).toBeInTheDocument();
    expect(screen.getByText("Сумма ещё не включена в баланс.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Открыть документ" })).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Зарегистрировать наличные" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Подтвердить наличные" })).not.toBeInTheDocument();
    expect(cashApi.getPlatformCashReceipt).not.toHaveBeenCalled();
  });

  it("disables both decisions for the employee's own registration", async () => {
    cashApi.listPlatformCashPayments.mockResolvedValue({
      items: [{ ...PENDING, can_approve: false }],
      total: 1,
      limit: 20,
      offset: 0,
    });
    renderWorkspace();

    const approve = await screen.findByRole("button", { name: "Подтвердить наличные" });
    expect(approve).toBeDisabled();
    expect(screen.getByRole("button", { name: "Отклонить" })).toBeDisabled();
    expect(screen.getByText(/должен подтвердить другой сотрудник/)).toBeInTheDocument();
    fireEvent.click(approve);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(cashApi.approvePlatformCashPayment).not.toHaveBeenCalled();
  });

  it("registers an advance without a saved subscription or invoice", async () => {
    cashApi.createPlatformCashPayment.mockResolvedValue(PENDING);
    renderWorkspace();
    const dialog = await openRegistration();
    fillRegistration(dialog);

    expect(within(dialog).getByLabelText("Зачисление")).toHaveValue("");
    fireEvent.click(within(dialog).getByRole("button", { name: "Передать на подтверждение" }));

    await waitFor(() =>
      expect(cashApi.createPlatformCashPayment).toHaveBeenCalledWith(TENANT_ID, {
        operation_id: OPERATION_ID,
        amount: "600.00",
        paid_at: new Date("2026-09-25T10:00").toISOString(),
        purpose: PENDING.purpose,
        target_invoice_id: null,
      }),
    );
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Оплата учитывается после подтверждения другим сотрудником Aurum",
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Открыть документ" })).not.toBeInTheDocument();
  });

  it("passes a selected invoice separately from the immutable cash amount", async () => {
    cashApi.createPlatformCashPayment.mockResolvedValue(PENDING);
    renderWorkspace({ invoices: [INVOICE] });
    const dialog = await openRegistration();
    fillRegistration(dialog);
    fireEvent.change(within(dialog).getByLabelText("Зачисление"), {
      target: { value: INVOICE.invoice_id },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Передать на подтверждение" }));

    await waitFor(() =>
      expect(cashApi.createPlatformCashPayment).toHaveBeenCalledWith(
        TENANT_ID,
        expect.objectContaining({ amount: "600.00", target_invoice_id: INVOICE.invoice_id }),
      ),
    );
  });

  it("blocks a zero amount before making a financial request", async () => {
    renderWorkspace();
    const dialog = await openRegistration();
    fillRegistration(dialog);
    fireEvent.change(within(dialog).getByLabelText("Сумма, TJS"), { target: { value: "0.00" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Передать на подтверждение" }));

    expect(await within(dialog).findByText("Сумма должна быть больше нуля")).toBeInTheDocument();
    expect(cashApi.createPlatformCashPayment).not.toHaveBeenCalled();
  });

  it("locks an ambiguous registration and retries the same operation and payload without claiming success", async () => {
    cashApi.createPlatformCashPayment
      .mockRejectedValueOnce(new Error("connection lost"))
      .mockResolvedValueOnce(PENDING);
    renderWorkspace();
    const dialog = await openRegistration();
    fillRegistration(dialog);
    fireEvent.click(within(dialog).getByRole("button", { name: "Передать на подтверждение" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Результат регистрации не подтверждён",
    );
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(within(dialog).getByLabelText("Сумма, TJS")).toBeDisabled();
    expect(within(dialog).getByLabelText("Назначение оплаты")).toBeDisabled();
    const firstPayload = cashApi.createPlatformCashPayment.mock.calls[0]?.[1];
    fireEvent.click(within(dialog).getByRole("button", { name: "Повторить отправку" }));

    await waitFor(() => expect(cashApi.createPlatformCashPayment).toHaveBeenCalledTimes(2));
    expect(cashApi.createPlatformCashPayment).toHaveBeenLastCalledWith(TENANT_ID, firstPayload);
    expect(await screen.findByRole("status")).toHaveTextContent("Регистрация сохранена");
  });

  it("requires an independent check before approving with the current row version", async () => {
    cashApi.approvePlatformCashPayment.mockResolvedValue(CONFIRMED);
    renderWorkspace();
    fireEvent.click(await screen.findByRole("button", { name: "Подтвердить наличные" }));
    const dialog = screen.getByRole("dialog", { name: "Подтвердить наличные" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Подтвердить получение" }));

    expect(await within(dialog).findByText("Подтвердите проверку наличных")).toBeInTheDocument();
    expect(cashApi.approvePlatformCashPayment).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole("checkbox"));
    fireEvent.click(within(dialog).getByRole("button", { name: "Подтвердить получение" }));

    await waitFor(() =>
      expect(cashApi.approvePlatformCashPayment).toHaveBeenCalledWith(TENANT_ID, PENDING.id, {
        operation_id: OPERATION_ID,
        expected_row_version: 1,
      }),
    );
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Наличные подтверждены и отражены в балансе",
    );
  });

  it("keeps a rejected server decision in the dialog without a success notice or receipt", async () => {
    cashApi.approvePlatformCashPayment.mockRejectedValue(new Error("stale version"));
    renderWorkspace();
    fireEvent.click(await screen.findByRole("button", { name: "Подтвердить наличные" }));
    const dialog = screen.getByRole("dialog", { name: "Подтвердить наличные" });
    fireEvent.click(within(dialog).getByRole("checkbox"));
    fireEvent.click(within(dialog).getByRole("button", { name: "Подтвердить получение" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Не удалось подтвердить результат",
    );
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Открыть документ" })).not.toBeInTheDocument();
  });

  it("rejects a registration only with an explicit reason", async () => {
    cashApi.rejectPlatformCashPayment.mockResolvedValue({ ...PENDING, status: "rejected" });
    renderWorkspace();
    fireEvent.click(await screen.findByRole("button", { name: "Отклонить" }));
    const dialog = screen.getByRole("dialog", { name: "Отклонить регистрацию" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Отклонить регистрацию" }));

    expect(await within(dialog).findByText(/минимум 5 символов/)).toBeInTheDocument();
    expect(cashApi.rejectPlatformCashPayment).not.toHaveBeenCalled();
    fireEvent.change(within(dialog).getByLabelText("Причина отклонения"), {
      target: { value: "Деньги фактически не получены" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Отклонить регистрацию" }));

    await waitFor(() =>
      expect(cashApi.rejectPlatformCashPayment).toHaveBeenCalledWith(TENANT_ID, PENDING.id, {
        operation_id: OPERATION_ID,
        expected_row_version: 1,
        reason: "Деньги фактически не получены",
      }),
    );
    expect(await screen.findByRole("status")).toHaveTextContent("Сумма не включена в баланс");
  });

  it("blocks all financial actions while offline or the journal is unsafe", async () => {
    renderWorkspace({ online: false });

    expect(screen.getByRole("button", { name: "Зарегистрировать наличные" })).toBeDisabled();
    expect(await screen.findByRole("button", { name: "Подтвердить наличные" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Отклонить" })).toBeDisabled();
    expect(screen.getByRole("status")).toHaveTextContent("Денежные действия временно недоступны");
  });
});
