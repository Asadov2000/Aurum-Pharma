import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const getCurrentSubscription = vi.fn();
const listInvoices = vi.fn();
const getInvoice = vi.fn();

vi.mock("@/features/billing/api", () => ({
  getCurrentSubscription: (...a: unknown[]) => getCurrentSubscription(...a),
  listInvoices: (...a: unknown[]) => listInvoices(...a),
  getInvoice: (...a: unknown[]) => getInvoice(...a),
  listPlans: vi.fn().mockResolvedValue([]),
  createSubscription: vi.fn(),
  createInvoice: vi.fn(),
  recordPayment: vi.fn(),
}));

import { BillingPage } from "@/features/billing/BillingPage";

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <BillingPage />
    </QueryClientProvider>,
  );
}

const SUB = {
  id: "sub-1",
  tenant_id: "t-1",
  plan_id: "p-1",
  status: "active" as const,
  billing_period: "monthly" as const,
  period_start: "2026-05-01T00:00:00Z",
  period_end: "2026-06-01T00:00:00Z",
  branches_count: 2,
  amount: "1100.00",
  currency: "TJS",
  cancelled_at: null,
  plan_name: "Aurum Pharma",
  plan_code: "aurum_pharma",
  plan_features: null,
};

const INV = {
  id: "inv-1",
  tenant_id: "t-1",
  subscription_id: SUB.id,
  invoice_number: "INV-2026-001",
  issued_at: "2026-05-15T00:00:00Z",
  due_at: "2026-05-22T00:00:00Z",
  amount: "1100.00",
  currency: "TJS",
  discount_amount: "0.00",
  discount_reason: null,
  status: "pending" as const,
  paid_at: null,
  notes: null,
};

const PAID_INV = {
  ...INV,
  id: "inv-2",
  invoice_number: "INV-2025-002",
  issued_at: "2025-12-01T00:00:00Z",
  due_at: "2025-12-08T00:00:00Z",
  status: "paid" as const,
  paid_at: "2025-12-03T08:30:00Z",
};

describe("BillingPage", () => {
  beforeEach(() => {
    getCurrentSubscription.mockReset();
    listInvoices.mockReset();
    getInvoice.mockReset();
    window.localStorage.clear();
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("shows a hint when there is no subscription yet", async () => {
    getCurrentSubscription.mockResolvedValueOnce(null);
    listInvoices.mockResolvedValueOnce([]);
    renderPage();
    expect(await screen.findAllByText("Подписка не подключена")).not.toHaveLength(0);
    expect(screen.getByText(/Свяжитесь с поддержкой Aurum Pharma/i)).toBeInTheDocument();
    expect(await screen.findAllByText(/Счетов пока нет/i)).not.toHaveLength(0);
  });

  it("renders the console overview with subscription and a pending invoice", async () => {
    getCurrentSubscription.mockResolvedValueOnce(SUB);
    listInvoices.mockResolvedValueOnce([INV]);
    renderPage();
    expect(screen.getByRole("heading", { level: 1, name: "Тариф и оплата" })).toBeInTheDocument();
    expect(await screen.findByText("Aurum Pharma")).toBeInTheDocument();
    expect(screen.getAllByText("INV-2026-001")).not.toHaveLength(0);
    expect(screen.getByText("Активен")).toBeInTheDocument();
    expect(screen.getByText("Есть счёт, ожидающий оплаты")).toBeInTheDocument();
    expect(screen.getAllByText("Ожидает оплаты")).not.toHaveLength(0);
  });

  it("prioritizes an overdue invoice over a pending invoice", async () => {
    const overdueInvoice = {
      ...INV,
      id: "inv-overdue",
      invoice_number: "INV-2026-OVERDUE",
      due_at: "2026-05-10T00:00:00Z",
      status: "overdue" as const,
    };
    getCurrentSubscription.mockResolvedValueOnce(SUB);
    listInvoices.mockResolvedValueOnce([INV, overdueInvoice]);
    renderPage();

    expect(await screen.findByText("Оплата просрочена")).toBeInTheDocument();
    expect(screen.getAllByText("INV-2026-OVERDUE")).not.toHaveLength(0);
    expect(screen.getAllByText("2 счёта")).not.toHaveLength(0);
  });

  it("shows a settled state when no invoice is pending", async () => {
    getCurrentSubscription.mockResolvedValueOnce(SUB);
    listInvoices.mockResolvedValueOnce([PAID_INV]);
    renderPage();

    expect(await screen.findByText("Расчёты актуальны")).toBeInTheDocument();
    expect(screen.getAllByText("Открытых счетов нет")).not.toHaveLength(0);
    expect(screen.getByText("Нет счетов, ожидающих оплаты.")).toBeInTheDocument();
  });

  it("keeps financial values and actions hidden while billing data is loading", () => {
    getCurrentSubscription.mockReturnValueOnce(new Promise(() => undefined));
    listInvoices.mockReturnValueOnce(new Promise(() => undefined));
    renderPage();

    expect(screen.getAllByRole("status").length).toBeGreaterThan(0);
    expect(screen.queryByText(/0[\s\u00a0],00 TJS/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Открыть счёт/i })).not.toBeInTheDocument();
  });

  it("opens the invoice detail modal on row click", async () => {
    getCurrentSubscription.mockResolvedValueOnce(SUB);
    listInvoices.mockResolvedValueOnce([INV]);
    getInvoice.mockResolvedValueOnce({
      ...INV,
      payments: [
        {
          id: "payment-1",
          invoice_id: INV.id,
          amount: "1100.00",
          currency: "TJS",
          method: "bank_transfer",
          reference: "RBKTJ-2026-001",
          paid_at: "2026-05-20T08:30:00Z",
          notes: null,
          created_at: "2026-05-20T08:30:00Z",
        },
      ],
    });
    renderPage();
    const invoiceButtons = await screen.findAllByRole("button", {
      name: "Открыть счёт INV-2026-001",
    });
    fireEvent.click(invoiceButtons[0]!);
    expect(await screen.findByText(/Счёт № INV-2026-001/i)).toBeInTheDocument();
    expect(screen.getAllByText("Банковский перевод")).not.toHaveLength(0);
    expect(screen.getAllByText(/1[\s\u00a0]100,00 TJS/)).not.toHaveLength(0);
    expect(getInvoice).toHaveBeenCalledWith(INV.id);
  });

  it("filters invoice history by status, year, and invoice number", async () => {
    getCurrentSubscription.mockResolvedValueOnce(SUB);
    listInvoices.mockResolvedValueOnce([INV, PAID_INV]);
    renderPage();

    const history = await screen.findByRole("region", { name: "История счетов" });
    fireEvent.change(screen.getByLabelText("Статус"), { target: { value: "paid" } });

    await waitFor(() => {
      expect(within(history).queryAllByText(INV.invoice_number)).toHaveLength(0);
    });
    expect(within(history).getAllByText(PAID_INV.invoice_number).length).toBeGreaterThan(0);

    fireEvent.change(screen.getByLabelText("Статус"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("Год"), { target: { value: "2026" } });
    fireEvent.change(screen.getByLabelText("Номер счёта"), { target: { value: "not-found" } });

    expect(await within(history).findByText("Счета не найдены")).toBeInTheDocument();
    expect(listInvoices).toHaveBeenCalledTimes(1);
  });

  it("renders long invoice histories in small pages", async () => {
    const manyInvoices = Array.from({ length: 11 }, (_, index) => ({
      ...PAID_INV,
      id: `inv-${index + 1}`,
      invoice_number: `INV-2025-${String(index + 1).padStart(3, "0")}`,
    }));
    getCurrentSubscription.mockResolvedValueOnce(SUB);
    listInvoices.mockResolvedValueOnce(manyInvoices);
    renderPage();

    const history = await screen.findByRole("region", { name: "История счетов" });
    await within(history).findAllByText("INV-2025-001");
    expect(within(history).queryAllByText("INV-2025-011")).toHaveLength(0);

    fireEvent.click(within(history).getByRole("button", { name: /Вперёд/i }));

    expect(await within(history).findAllByText("INV-2025-011")).not.toHaveLength(0);
    expect(within(history).getByText("2 из 2")).toBeInTheDocument();
  });

  it("lets the user retry a failed subscription request", async () => {
    getCurrentSubscription.mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce(SUB);
    listInvoices.mockResolvedValueOnce([]);
    renderPage();

    expect(await screen.findByRole("alert")).toHaveTextContent("Не удалось загрузить подписку");
    fireEvent.click(screen.getByRole("button", { name: "Повторить" }));

    await waitFor(() => expect(getCurrentSubscription).toHaveBeenCalledTimes(2));
    expect(await screen.findByText("Aurum Pharma")).toBeInTheDocument();
  });

  it("keeps subscription data visible and retries only a failed invoice request", async () => {
    getCurrentSubscription.mockResolvedValueOnce(SUB);
    listInvoices.mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce([]);
    renderPage();

    expect(await screen.findByText("Aurum Pharma")).toBeInTheDocument();
    const alerts = screen.getAllByRole("alert");
    const invoiceAlert = alerts.find((alert) =>
      alert.textContent?.includes("Не удалось загрузить состояние расчётов"),
    );
    expect(invoiceAlert).toBeDefined();
    fireEvent.click(within(invoiceAlert!).getByRole("button", { name: "Повторить" }));

    await waitFor(() => expect(listInvoices).toHaveBeenCalledTimes(2));
    expect(getCurrentSubscription).toHaveBeenCalledTimes(1);
  });
});
