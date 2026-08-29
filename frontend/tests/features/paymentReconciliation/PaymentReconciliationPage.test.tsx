import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const listPaymentReconciliation = vi.fn();
const confirmPaymentAttempt = vi.fn();
const voidPaymentAttempt = vi.fn();

vi.mock("@/features/auth/filterPreferences", () => ({
  useFilterPreferenceKey: () => "test:payment-reconciliation",
}));

vi.mock("@/features/paymentReconciliation/api", () => ({
  listPaymentReconciliation: (...args: unknown[]) => listPaymentReconciliation(...args),
}));

vi.mock("@/features/pos/api", () => ({
  confirmPaymentAttempt: (...args: unknown[]) => confirmPaymentAttempt(...args),
  voidPaymentAttempt: (...args: unknown[]) => voidPaymentAttempt(...args),
}));

import PaymentReconciliationPage from "@/features/paymentReconciliation/PaymentReconciliationPage";

const ITEM = {
  id: "00000000-0000-4000-8000-000000000001",
  sale_id: "00000000-0000-4000-8000-000000000002",
  branch_id: "00000000-0000-4000-8000-000000000003",
  branch_name: "Аптека Рудаки",
  register_id: "00000000-0000-4000-8000-000000000004",
  register_name: "Касса 01",
  cashier_name: "Сорбон Ахмедов",
  payment_method: "card" as const,
  amount: "59.00",
  sale_total_amount: "59.00",
  currency: "TJS" as const,
  status: "requires_reconciliation" as const,
  item_count: 2,
  created_at: "2026-08-30T08:00:00Z",
  reconciliation_started_at: "2026-08-30T08:01:00Z",
  confirmed_at: null,
};

function response(status = ITEM.status) {
  return {
    items: [{ ...ITEM, status }],
    total: 1,
    page: 1,
    page_size: 25,
    summary: {
      requires_reconciliation_count: status === "requires_reconciliation" ? 1 : 0,
      requires_reconciliation_amount: status === "requires_reconciliation" ? "59.00" : "0.00",
      confirmed_count: status === "confirmed" ? 1 : 0,
      confirmed_amount: status === "confirmed" ? "59.00" : "0.00",
    },
    branches: [{ id: ITEM.branch_id, name: ITEM.branch_name }],
  };
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <PaymentReconciliationPage />
    </QueryClientProvider>,
  );
}

describe("PaymentReconciliationPage", () => {
  beforeEach(() => {
    localStorage.clear();
    listPaymentReconciliation.mockReset();
    listPaymentReconciliation.mockResolvedValue(response());
    confirmPaymentAttempt.mockReset();
    confirmPaymentAttempt.mockResolvedValue({ ...ITEM, status: "confirmed" });
    voidPaymentAttempt.mockReset();
    voidPaymentAttempt.mockResolvedValue({ ...ITEM, status: "voided" });
  });

  it("requires evidence and explicit confirmation before confirming payment", async () => {
    renderPage();

    expect((await screen.findAllByText("Аптека Рудаки")).length).toBeGreaterThan(0);
    fireEvent.click(screen.getAllByRole("button", { name: "Принять решение" })[0]!);
    fireEvent.change(screen.getByLabelText("Терминал"), { target: { value: "TERM-01" } });
    fireEvent.change(screen.getByLabelText("Номер операции/документа"), {
      target: { value: "BANK-100500" },
    });
    fireEvent.click(
      screen.getByRole("checkbox", {
        name: "Подтверждаю, что проверил журнал терминала и указал фактический результат оплаты",
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить оплату" }));

    await waitFor(() => expect(confirmPaymentAttempt).toHaveBeenCalledTimes(1));
    expect(confirmPaymentAttempt).toHaveBeenCalledWith(ITEM.id, {
      terminal_id: "TERM-01",
      external_reference: "BANK-100500",
    });
    expect(voidPaymentAttempt).not.toHaveBeenCalled();
  });

  it("keeps confirmed payments visible without a second manager action", async () => {
    listPaymentReconciliation.mockResolvedValue(response("confirmed"));
    renderPage();

    expect((await screen.findAllByText("Оплата подтверждена")).length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: "Принять решение" })).not.toBeInTheDocument();
    expect(screen.getByText("Ждут завершения чека")).toBeInTheDocument();
  });

  it("records an unpaid decision as a manager override with evidence", async () => {
    renderPage();

    await screen.findByText("Сорбон Ахмедов");
    fireEvent.click(screen.getAllByRole("button", { name: "Принять решение" })[0]!);
    fireEvent.click(screen.getByRole("button", { name: "Оплаты нет" }));
    fireEvent.change(screen.getByLabelText("Терминал"), { target: { value: "TERM-02" } });
    fireEvent.change(screen.getByLabelText("Номер операции/документа"), {
      target: { value: "JOURNAL-404" },
    });
    fireEvent.change(screen.getByLabelText("Комментарий (необязательно)"), {
      target: { value: "Операции нет в журнале" },
    });
    fireEvent.click(
      screen.getByRole("checkbox", {
        name: "Подтверждаю, что проверил журнал терминала и указал фактический результат оплаты",
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Зафиксировать отсутствие оплаты" }));

    await waitFor(() => expect(voidPaymentAttempt).toHaveBeenCalledTimes(1));
    expect(voidPaymentAttempt).toHaveBeenCalledWith(ITEM.id, {
      reason: "manager_override",
      terminal_id: "TERM-02",
      external_reference: "JOURNAL-404",
      operator_note: "Операции нет в журнале",
    });
    expect(confirmPaymentAttempt).not.toHaveBeenCalled();
  });
});
