import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const getFinancialAccount = vi.fn();
const listPaymentSubmissions = vi.fn();
const createPaymentSubmission = vi.fn();
const withdrawPaymentSubmission = vi.fn();
const listTenantCashPayments = vi.fn();
const getTenantCashReceipt = vi.fn();
const authState = vi.hoisted(() => ({
  user: {
    is_developer: false,
    support_access: null,
    permissions: [] as string[],
  },
}));

vi.mock("@/features/billing/api", () => ({
  getFinancialAccount: (...args: unknown[]) => getFinancialAccount(...args),
  listPaymentSubmissions: (...args: unknown[]) => listPaymentSubmissions(...args),
  createPaymentSubmission: (...args: unknown[]) => createPaymentSubmission(...args),
  withdrawPaymentSubmission: (...args: unknown[]) => withdrawPaymentSubmission(...args),
}));

vi.mock("@/features/auth/hooks", () => ({
  useAuth: () => authState,
}));

vi.mock("@/features/billing/cashApi", () => ({
  listTenantCashPayments: (...args: unknown[]) => listTenantCashPayments(...args),
  getTenantCashReceipt: (...args: unknown[]) => getTenantCashReceipt(...args),
}));

import { BillingPage } from "@/features/billing/BillingPage";

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <BillingPage />
    </QueryClientProvider>,
  );
}

const SUBSCRIPTION = {
  status: "active" as const,
  plan_name: "Aurum Pharma",
  billing_period: "monthly" as const,
  period_start: "2026-05-01T00:00:00Z",
  period_end: "2026-06-01T00:00:00Z",
  branches_count: 2,
  amount: "1100.00",
  currency: "TJS" as const,
};

const INVOICE = {
  invoice_id: "inv-1",
  invoice_number: "AP-2026-001",
  document_state: "issued" as const,
  settlement_state: "unpaid" as const,
  collection_state: "not_due" as const,
  period_start: "2026-05-01T00:00:00Z",
  period_end: "2026-06-01T00:00:00Z",
  issued_at: "2026-05-15T00:00:00Z",
  due_at: "2026-05-22T00:00:00Z",
  total_amount: "1100.00",
  outstanding_amount: "1100.00",
  currency: "TJS" as const,
};

const PAID_INVOICE = {
  ...INVOICE,
  invoice_id: "inv-2",
  invoice_number: "AP-2025-002",
  issued_at: "2025-12-01T00:00:00Z",
  due_at: "2025-12-08T00:00:00Z",
  settlement_state: "paid" as const,
  outstanding_amount: "0.00",
};

const PAYMENT = {
  amount: "1100.00",
  allocated_amount: "1000.00",
  credit_amount: "100.00",
  corrected_amount: "0.00",
  refunded_amount: "0.00",
  currency: "TJS" as const,
  paid_at: "2026-05-20T08:30:00Z",
  confirmed_at: "2026-05-20T09:00:00Z",
  lifecycle_state: "confirmed" as const,
};

const ACCOUNT = {
  subscription: SUBSCRIPTION,
  currency: "TJS" as const,
  outstanding_amount: "1100.00",
  credit_balance: "0.00",
  invoices: [INVOICE],
  payments: [],
};

const SUBMISSION = {
  submission_id: "submission-1",
  tenant_id: "tenant-1",
  target_invoice_id: INVOICE.invoice_id,
  invoice_number: INVOICE.invoice_number,
  amount: "1100.00",
  currency: "TJS" as const,
  paid_at: "2026-05-20T08:30:00Z",
  reference_suffix: "0125",
  status: "submitted" as const,
  row_version: 1,
  created_at: "2026-05-20T08:31:00Z",
  can_withdraw: true,
};

describe("BillingPage", () => {
  beforeEach(() => {
    getFinancialAccount.mockReset();
    listPaymentSubmissions.mockReset();
    createPaymentSubmission.mockReset();
    withdrawPaymentSubmission.mockReset();
    listTenantCashPayments.mockReset();
    getTenantCashReceipt.mockReset();
    listTenantCashPayments.mockResolvedValue({ items: [], total: 0, limit: 20, offset: 0 });
    authState.user.permissions = [];
    listPaymentSubmissions.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 10 });
    window.localStorage.clear();
  });

  afterEach(() => {
    vi.clearAllMocks();
    vi.restoreAllMocks();
  });

  it("shows an empty payments account without a subscription call to action", async () => {
    getFinancialAccount.mockResolvedValueOnce({
      subscription: null,
      currency: "TJS",
      outstanding_amount: "0.00",
      credit_balance: "0.00",
      invoices: [],
      payments: [],
    });
    renderPage();

    expect(await screen.findByText("Состояние расчётов")).toBeInTheDocument();
    expect(screen.getByText("Подтвержденных платежей пока нет")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "История счетов" })).not.toBeInTheDocument();
    expect(
      screen.queryByText(/подключить подписку|продлить подписку|разблокировать доступ/i),
    ).not.toBeInTheDocument();
    expect(listPaymentSubmissions).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Открыть архив счетов" }));
    expect(screen.getByText("Счетов пока нет")).toBeInTheDocument();
  });

  it("renders the financial account and an unpaid invoice", async () => {
    getFinancialAccount.mockResolvedValueOnce(ACCOUNT);
    renderPage();

    expect(screen.getByRole("heading", { level: 1, name: "Расчёты с Aurum" })).toBeInTheDocument();
    expect(await screen.findByText("Состояние расчётов")).toBeInTheDocument();
    expect(screen.queryByText("AP-2026-001")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Архив счетов", exact: true }));
    expect(screen.getAllByText("AP-2026-001")).not.toHaveLength(0);
    expect(screen.getAllByText("Ожидает оплаты")).not.toHaveLength(0);
    expect(screen.getAllByText(/1[\s\u00a0]100,00 TJS/)).not.toHaveLength(0);
  });

  it("keeps confirmed payments as the default and opens bank submissions only in the archive", async () => {
    getFinancialAccount.mockResolvedValueOnce({ ...ACCOUNT, payments: [PAYMENT] });
    renderPage();

    const payments = await screen.findByRole("region", { name: "Подтвержденные платежи" });
    expect(within(payments).getAllByText("Подтвержден")).not.toHaveLength(0);
    expect(screen.queryByRole("region", { name: "История счетов" })).not.toBeInTheDocument();
    expect(listPaymentSubmissions).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Архив счетов", exact: true }));
    expect(await screen.findByText("Заявки об оплате")).toBeInTheDocument();
    expect(
      screen.queryByRole("region", { name: "Подтвержденные платежи" }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Оплаты", exact: true }));
    expect(
      await screen.findByRole("region", { name: "Подтвержденные платежи" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "История счетов" })).not.toBeInTheDocument();
    expect(getFinancialAccount).toHaveBeenCalledTimes(1);
  });

  it("does not turn a saved suspended subscription into a payment or access prompt", async () => {
    getFinancialAccount.mockResolvedValueOnce({
      ...ACCOUNT,
      subscription: { ...SUBSCRIPTION, status: "suspended" },
    });
    renderPage();

    expect(await screen.findByText("Состояние расчётов")).toBeInTheDocument();
    expect(screen.getByText("Остаток к оплате")).toBeInTheDocument();
    expect(screen.getByText("Аванс")).toBeInTheDocument();
    expect(
      screen.queryByText(/продлить подписку|восстановить доступ|приостановлена/i),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /оплатить|подтвердить/i })).not.toBeInTheDocument();
  });

  it("prioritizes overdue debt and shows the exact outstanding amount", async () => {
    getFinancialAccount.mockResolvedValueOnce({
      ...ACCOUNT,
      outstanding_amount: "250.00",
      invoices: [
        INVOICE,
        {
          ...INVOICE,
          invoice_id: "inv-overdue",
          invoice_number: "AP-2026-OVERDUE",
          collection_state: "overdue",
          settlement_state: "partially_paid",
          outstanding_amount: "250.00",
        },
      ],
    });
    renderPage();

    expect(await screen.findByText("Состояние расчётов")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Архив счетов", exact: true }));
    expect(screen.getAllByText("AP-2026-OVERDUE")).not.toHaveLength(0);
    expect(screen.getAllByText(/250,00 TJS/)).not.toHaveLength(0);
  });

  it("does not render zero financial placeholders while data is loading", () => {
    getFinancialAccount.mockReturnValueOnce(new Promise(() => undefined));
    renderPage();

    expect(screen.getByLabelText("Загрузка расчетов")).toBeInTheDocument();
    expect(screen.queryByText(/0[\s\u00a0],00 TJS/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Открыть счет/i })).not.toBeInTheDocument();
  });

  it("opens invoice details without a second network request", async () => {
    getFinancialAccount.mockResolvedValueOnce(ACCOUNT);
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Архив счетов", exact: true }));
    const buttons = await screen.findAllByRole("button", { name: "Открыть счет AP-2026-001" });
    fireEvent.click(buttons[0]!);

    expect(await screen.findByText("Счет № AP-2026-001")).toBeInTheDocument();
    expect(screen.getByText("Расчетный период")).toBeInTheDocument();
    expect(screen.getByText("Осталось оплатить")).toBeInTheDocument();
    const dialog = screen.getByRole("dialog", { name: "Счет № AP-2026-001" });
    expect(
      within(dialog).queryByRole("button", { name: /оплатить|подтвердить|изменить/i }),
    ).not.toBeInTheDocument();
    expect(within(dialog).queryByRole("textbox")).not.toBeInTheDocument();
    expect(getFinancialAccount).toHaveBeenCalledTimes(1);
  });

  it("filters invoice history by status, year, and number locally", async () => {
    getFinancialAccount.mockResolvedValueOnce({
      ...ACCOUNT,
      invoices: [INVOICE, PAID_INVOICE],
    });
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "Архив счетов", exact: true }));
    const history = await screen.findByRole("region", { name: "История счетов" });
    fireEvent.click(screen.getByRole("button", { name: /^Фильтры/ }));
    fireEvent.change(screen.getByLabelText("Статус"), { target: { value: "paid" } });
    await waitFor(() => {
      expect(within(history).queryAllByText(INVOICE.invoice_number)).toHaveLength(0);
    });
    expect(within(history).getAllByText(PAID_INVOICE.invoice_number).length).toBeGreaterThan(0);

    fireEvent.change(screen.getByLabelText("Статус"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("Год"), { target: { value: "2026" } });
    fireEvent.change(screen.getByLabelText("Номер счёта"), { target: { value: "not-found" } });

    expect(await within(history).findByText("Счета не найдены")).toBeInTheDocument();
    expect(getFinancialAccount).toHaveBeenCalledTimes(1);
  });

  it("paginates long invoice histories", async () => {
    const invoices = Array.from({ length: 11 }, (_, index) => ({
      ...PAID_INVOICE,
      invoice_id: `inv-${index + 1}`,
      invoice_number: `AP-2025-${String(index + 1).padStart(3, "0")}`,
    }));
    getFinancialAccount.mockResolvedValueOnce({ ...ACCOUNT, invoices });
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "Архив счетов", exact: true }));
    const history = await screen.findByRole("region", { name: "История счетов" });
    await within(history).findAllByText("AP-2025-001");
    expect(within(history).queryAllByText("AP-2025-011")).toHaveLength(0);
    fireEvent.click(within(history).getByRole("button", { name: "Вперёд" }));

    expect(await within(history).findAllByText("AP-2025-011")).not.toHaveLength(0);
    expect(within(history).getByText("2 из 2")).toBeInTheDocument();
  });

  it("shows confirmed payment allocation and credit without bank details", async () => {
    getFinancialAccount.mockResolvedValueOnce({ ...ACCOUNT, payments: [PAYMENT] });
    renderPage();

    expect(await screen.findByText("Подтвержденные платежи")).toBeInTheDocument();
    expect(screen.getAllByText("Подтвержден")).not.toHaveLength(0);
    expect(screen.getAllByText(/1[\s\u00a0]000,00 TJS/)).not.toHaveLength(0);
    expect(screen.getAllByText(/100,00 TJS/)).not.toHaveLength(0);
    expect(screen.queryByText(/reference|реквизит/i)).not.toBeInTheDocument();
  });

  it("distinguishes written-off invoices from paid invoices", async () => {
    getFinancialAccount.mockResolvedValueOnce({
      ...ACCOUNT,
      outstanding_amount: "0.00",
      invoices: [
        {
          ...PAID_INVOICE,
          invoice_id: "inv-written-off",
          invoice_number: "AP-2025-WRITEOFF",
          settlement_state: "written_off",
        },
      ],
    });
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "Архив счетов", exact: true }));
    const invoiceRow = await screen.findByRole("row", { name: /AP-2025-WRITEOFF/ });
    expect(within(invoiceRow).getByText("Списан")).toBeInTheDocument();
    expect(within(invoiceRow).queryByText("Оплачен")).not.toBeInTheDocument();
  });

  it("retries a failed financial account request", async () => {
    getFinancialAccount.mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce(ACCOUNT);
    renderPage();

    expect(await screen.findByRole("alert")).toHaveTextContent("Не удалось загрузить расчеты");
    fireEvent.click(screen.getByRole("button", { name: "Повторить" }));

    await waitFor(() => expect(getFinancialAccount).toHaveBeenCalledTimes(2));
    expect(await screen.findByText("Состояние расчётов")).toBeInTheDocument();
  });

  it("fully hides create and withdraw actions without tenant permissions", async () => {
    getFinancialAccount.mockResolvedValueOnce(ACCOUNT);
    listPaymentSubmissions.mockResolvedValueOnce({
      items: [SUBMISSION],
      total: 1,
      page: 1,
      page_size: 10,
    });
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "Архив счетов", exact: true }));
    expect(await screen.findByText("Заявки об оплате")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Сообщить об оплате" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Отозвать" })).not.toBeInTheDocument();
  });

  it("keeps historical submissions read-only even with legacy write permissions", async () => {
    authState.user.permissions = [
      "billing.payment_submission.create",
      "billing.payment_submission.withdraw",
    ];
    getFinancialAccount.mockResolvedValueOnce(ACCOUNT);
    listPaymentSubmissions.mockResolvedValue({
      items: [SUBMISSION],
      total: 1,
      page: 1,
      page_size: 10,
    });
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Архив счетов", exact: true }));
    expect(await screen.findByText("Заявки об оплате")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Сообщить об оплате" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Отозвать" })).not.toBeInTheDocument();
    expect(createPaymentSubmission).not.toHaveBeenCalled();
    expect(withdrawPaymentSubmission).not.toHaveBeenCalled();
  });
});
