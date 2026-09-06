import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const getPlatformBillingOverview = vi.fn();
const listPlatformInvoices = vi.fn();
const listPlatformPricingPlans = vi.fn();
const createPlatformPricingPlan = vi.fn();
const createPlatformPricingPrice = vi.fn();
const schedulePlatformPricingPrice = vi.fn();
const activatePlatformPricingPrice = vi.fn();
const cancelPlatformPricingPrice = vi.fn();
const listPlatformBillingTenants = vi.fn();
const getPlatformFinancialAccount = vi.fn();
const listPlatformPaymentApprovalQueue = vi.fn();
const getPlatformPaymentApprovalDetail = vi.fn();
const createPlatformBankPaymentReview = vi.fn();
const approvePlatformBankPayment = vi.fn();
const rejectPlatformBankPaymentReview = vi.fn();
const listPlatformPaymentAdjustmentQueue = vi.fn();
const listPlatformPaymentSubmissions = vi.fn();
const getPlatformPaymentSubmission = vi.fn();
const reviewPlatformPaymentSubmission = vi.fn();
const rejectPlatformPaymentSubmission = vi.fn();
const createPlatformPaymentAdjustment = vi.fn();
const approvePlatformPaymentAdjustment = vi.fn();
const rejectPlatformPaymentAdjustment = vi.fn();
const authState = vi.hoisted(() => ({
  user: {
    id: "developer-1",
    is_developer: true,
    is_administrator: false,
    platform_capabilities: ["platform.billing.view"] as string[],
  },
}));

vi.mock("@/features/platformBilling/api", () => ({
  getPlatformBillingOverview: (...args: unknown[]) => getPlatformBillingOverview(...args),
  listPlatformInvoices: (...args: unknown[]) => listPlatformInvoices(...args),
  listPlatformPricingPlans: (...args: unknown[]) => listPlatformPricingPlans(...args),
  createPlatformPricingPlan: (...args: unknown[]) => createPlatformPricingPlan(...args),
  createPlatformPricingPrice: (...args: unknown[]) => createPlatformPricingPrice(...args),
  schedulePlatformPricingPrice: (...args: unknown[]) => schedulePlatformPricingPrice(...args),
  activatePlatformPricingPrice: (...args: unknown[]) => activatePlatformPricingPrice(...args),
  cancelPlatformPricingPrice: (...args: unknown[]) => cancelPlatformPricingPrice(...args),
  listPlatformBillingTenants: (...args: unknown[]) => listPlatformBillingTenants(...args),
  getPlatformFinancialAccount: (...args: unknown[]) => getPlatformFinancialAccount(...args),
  listPlatformPaymentApprovalQueue: (...args: unknown[]) =>
    listPlatformPaymentApprovalQueue(...args),
  getPlatformPaymentApprovalDetail: (...args: unknown[]) =>
    getPlatformPaymentApprovalDetail(...args),
  createPlatformBankPaymentReview: (...args: unknown[]) => createPlatformBankPaymentReview(...args),
  approvePlatformBankPayment: (...args: unknown[]) => approvePlatformBankPayment(...args),
  rejectPlatformBankPaymentReview: (...args: unknown[]) => rejectPlatformBankPaymentReview(...args),
  listPlatformPaymentAdjustmentQueue: (...args: unknown[]) =>
    listPlatformPaymentAdjustmentQueue(...args),
  listPlatformPaymentSubmissions: (...args: unknown[]) =>
    listPlatformPaymentSubmissions(...args),
  getPlatformPaymentSubmission: (...args: unknown[]) => getPlatformPaymentSubmission(...args),
  reviewPlatformPaymentSubmission: (...args: unknown[]) =>
    reviewPlatformPaymentSubmission(...args),
  rejectPlatformPaymentSubmission: (...args: unknown[]) =>
    rejectPlatformPaymentSubmission(...args),
  createPlatformPaymentAdjustment: (...args: unknown[]) => createPlatformPaymentAdjustment(...args),
  approvePlatformPaymentAdjustment: (...args: unknown[]) =>
    approvePlatformPaymentAdjustment(...args),
  rejectPlatformPaymentAdjustment: (...args: unknown[]) => rejectPlatformPaymentAdjustment(...args),
}));

vi.mock("@/features/auth/hooks", () => ({
  useAuth: () => authState,
}));

import { PlatformBillingPage } from "@/features/platformBilling/PlatformBillingPage";

const OVERVIEW = {
  generated_at: "2026-08-13T12:00:00Z",
  tenants_total: 8,
  active_subscriptions: 6,
  attention_subscriptions: 2,
  open_invoices: 3,
  overdue_invoices: 1,
  outstanding_amount: "740.00",
  currency: "TJS",
};

const INVOICE = {
  tenant_name: "Шифо Марказ",
  invoice_number: "INV-00000042",
  issued_at: "2026-08-01T00:00:00Z",
  due_at: "2026-08-10T00:00:00Z",
  amount: "550.00",
  paid_amount: "100.00",
  outstanding_amount: "450.00",
  currency: "TJS",
  status: "overdue" as const,
  subscription_status: "grace_period",
};

const PRICING_PLAN = {
  plan_id: "plan-1",
  code: "business",
  name: "Бизнес",
  description: "Для растущих аптечных сетей",
  currency: "TJS" as const,
  is_active: false,
  created_by: "developer-2",
  created_at: "2026-08-01T00:00:00Z",
  updated_at: "2026-08-01T00:00:00Z",
  versions: [
    {
      price_version_id: "price-1",
      plan_id: "plan-1",
      version_number: 1,
      status: "draft" as const,
      monthly_price_per_branch: "590.00",
      annual_discount_pct: "20.00",
      currency: "TJS" as const,
      audience: "default" as const,
      effective_from: null,
      notice_days: 30,
      change_reason: "Плановое обновление коммерческой цены.",
      created_by: "developer-2",
      approved_by: null,
      approved_at: null,
      activated_at: null,
      archived_at: null,
      row_version: 1,
      created_at: "2026-08-01T00:00:00Z",
    },
  ],
};

const BILLING_TENANT = {
  tenant_id: "11111111-1111-4111-8111-111111111111",
  name: "Шифо Марказ",
  tenant_status: "active",
  subscription_status: "active",
};

const FINANCIAL_INVOICE = {
  invoice_id: "22222222-2222-4222-8222-222222222222",
  tenant_id: BILLING_TENANT.tenant_id,
  subscription_id: "33333333-3333-4333-8333-333333333333",
  price_application_id: "44444444-4444-4444-8444-444444444444",
  price_application_kind: "initial" as const,
  invoice_number: "AF-2026-000042",
  document_state: "issued" as const,
  settlement_state: "unpaid" as const,
  collection_state: "overdue" as const,
  period_start: "2026-08-01T00:00:00Z",
  period_end: "2026-09-01T00:00:00Z",
  due_at: "2026-08-10T00:00:00Z",
  total_amount: "590.00",
  outstanding_amount: "590.00",
  currency: "TJS" as const,
  issued_at: "2026-08-01T00:00:00Z",
};

const FINANCIAL_ACCOUNT = {
  tenant_id: BILLING_TENANT.tenant_id,
  currency: "TJS" as const,
  outstanding_amount: "590.00",
  credit_balance: "0.00",
  invoices: [FINANCIAL_INVOICE],
  payments: [],
  journal_balanced: true,
};

const PAYMENT_SUBMISSION = {
  submission_id: "submission-1",
  tenant_id: BILLING_TENANT.tenant_id,
  target_invoice_id: FINANCIAL_INVOICE.invoice_id,
  invoice_number: FINANCIAL_INVOICE.invoice_number,
  amount: "590.00",
  currency: "TJS" as const,
  paid_at: "2026-08-14T08:30:00Z",
  reference_suffix: "9001",
  status: "submitted" as const,
  row_version: 1,
  created_at: "2026-08-14T08:31:00Z",
  can_withdraw: true,
};

const PRIVATE_SUBMISSION_REFERENCE = "TJ-PRIVATE-SUBMISSION-9001";
const PAYMENT_SUBMISSION_DETAIL = {
  ...PAYMENT_SUBMISSION,
  tenant_name: BILLING_TENANT.name,
  external_reference: PRIVATE_SUBMISSION_REFERENCE,
};

const PAYMENT_SUBMISSION_DETAIL_KEY = [
  "platform-billing",
  "v2",
  "submission-detail",
  BILLING_TENANT.tenant_id,
  PAYMENT_SUBMISSION.submission_id,
];

const PRIVATE_APPROVAL_REFERENCE = "TJPRIVATEAPPROVAL9001";

function paymentApprovalDetail(reviewId: string, rowVersion = 1) {
  return {
    review_id: reviewId,
    tenant_id: BILLING_TENANT.tenant_id,
    tenant_name: BILLING_TENANT.name,
    target_invoice_id: FINANCIAL_INVOICE.invoice_id,
    invoice_number: FINANCIAL_INVOICE.invoice_number,
    amount: "200.00",
    currency: "TJS" as const,
    paid_at: "2026-08-14T08:35:00Z",
    recipient_account_key: "aurum_tjs_primary" as const,
    external_reference: PRIVATE_APPROVAL_REFERENCE,
    status: "pending_approval" as const,
    row_version: rowVersion,
    created_at: "2026-08-14T08:36:00Z",
    is_own_review: false,
  };
}

const PAYMENT = {
  payment_id: "77777777-7777-4777-8777-777777777777",
  amount: "700.00",
  allocated_amount: "590.00",
  credit_amount: "110.00",
  corrected_amount: "0.00",
  refunded_amount: "0.00",
  reversible_amount: "700.00",
  adjustment_pending: false,
  currency: "TJS" as const,
  paid_at: "2026-08-14T08:30:00Z",
  confirmed_at: "2026-08-14T08:35:00Z",
  lifecycle_state: "confirmed" as const,
};

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const view = render(
    <QueryClientProvider client={client}>
      <PlatformBillingPage />
    </QueryClientProvider>,
  );
  return { ...view, queryClient: client };
}

describe("PlatformBillingPage", () => {
  beforeEach(() => {
    window.localStorage.clear();
    getPlatformBillingOverview.mockReset();
    listPlatformInvoices.mockReset();
    listPlatformPricingPlans.mockReset();
    createPlatformPricingPlan.mockReset();
    createPlatformPricingPrice.mockReset();
    schedulePlatformPricingPrice.mockReset();
    activatePlatformPricingPrice.mockReset();
    cancelPlatformPricingPrice.mockReset();
    listPlatformBillingTenants.mockReset();
    getPlatformFinancialAccount.mockReset();
    listPlatformPaymentApprovalQueue.mockReset();
    getPlatformPaymentApprovalDetail.mockReset();
    createPlatformBankPaymentReview.mockReset();
    approvePlatformBankPayment.mockReset();
    rejectPlatformBankPaymentReview.mockReset();
    listPlatformPaymentAdjustmentQueue.mockReset();
    listPlatformPaymentSubmissions.mockReset();
    getPlatformPaymentSubmission.mockReset();
    reviewPlatformPaymentSubmission.mockReset();
    rejectPlatformPaymentSubmission.mockReset();
    createPlatformPaymentAdjustment.mockReset();
    approvePlatformPaymentAdjustment.mockReset();
    rejectPlatformPaymentAdjustment.mockReset();
    authState.user.platform_capabilities = ["platform.billing.view"];
    getPlatformBillingOverview.mockResolvedValue(OVERVIEW);
    listPlatformInvoices.mockResolvedValue({
      items: [INVOICE],
      total: 1,
      page: 1,
      page_size: 20,
    });
    listPlatformPricingPlans.mockResolvedValue({
      items: [PRICING_PLAN],
      total: 1,
      page: 1,
      page_size: 20,
    });
    listPlatformBillingTenants.mockResolvedValue({
      items: [BILLING_TENANT],
      total: 1,
      page: 1,
      page_size: 20,
    });
    getPlatformFinancialAccount.mockResolvedValue(FINANCIAL_ACCOUNT);
    listPlatformPaymentApprovalQueue.mockResolvedValue({
      items: [],
      total: 0,
      page: 1,
      page_size: 20,
    });
    getPlatformPaymentApprovalDetail.mockImplementation(
      (_tenantId: string, reviewId: string) =>
        Promise.resolve(paymentApprovalDetail(reviewId, reviewId === "review-other" ? 2 : 1)),
    );
    listPlatformPaymentAdjustmentQueue.mockResolvedValue({
      items: [],
      total: 0,
      page: 1,
      page_size: 20,
    });
    listPlatformPaymentSubmissions.mockResolvedValue({
      items: [],
      total: 0,
      page: 1,
      page_size: 20,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows a useful read-only summary without financial mutation controls", async () => {
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "Архив прежних счетов" }));

    expect(
      await screen.findByRole("heading", { level: 1, name: "Расчёты Aurum" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Только чтение")).toBeInTheDocument();
    expect(
      within(await screen.findByLabelText("Сводка расчётов")).getByText("740,00 TJS"),
    ).toBeInTheDocument();
    expect(screen.getAllByText("Шифо Марказ").length).toBeGreaterThan(0);
    expect(screen.getAllByText("450,00 TJS").length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: /подтвердить оплату/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /создать счёт/i })).not.toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/[0-9a-f]{8}-[0-9a-f-]{27}/i);
  });

  it("sends status and debounced search through the server-side filter contract", async () => {
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "Архив прежних счетов" }));
    await screen.findByText("Только чтение");

    fireEvent.click(screen.getByRole("button", { name: /^Фильтры/ }));
    fireEvent.change(screen.getByLabelText("Статус"), { target: { value: "paid" } });
    fireEvent.change(screen.getByLabelText("Аптека или номер счёта"), {
      target: { value: "Шифо" },
    });

    await waitFor(
      () => {
        expect(listPlatformInvoices).toHaveBeenCalledWith(
          expect.objectContaining({ q: "Шифо", status: "paid", page: 1, page_size: 20 }),
          expect.any(AbortSignal),
        );
      },
      { timeout: 1500 },
    );
  });

  it("keeps the invoice error independent from the summary and retries only the register", async () => {
    listPlatformInvoices.mockRejectedValueOnce(new Error("network unavailable"));
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "Архив прежних счетов" }));

    expect(await screen.findByText("740,00 TJS")).toBeInTheDocument();
    expect(await screen.findByRole("alert")).toHaveTextContent("Не удалось загрузить счета");
    listPlatformInvoices.mockResolvedValueOnce({ items: [], total: 0, page: 1, page_size: 20 });
    fireEvent.click(within(screen.getByRole("alert")).getByRole("button", { name: "Повторить" }));

    await waitFor(() => expect(listPlatformInvoices).toHaveBeenCalledTimes(2));
    expect(await screen.findByText("Счета не найдены")).toBeInTheDocument();
  });

  it("keeps pricing mutations hidden for a view-only platform grant", async () => {
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "Тарифы и цены" }));

    expect(await screen.findByText("Бизнес")).toBeInTheDocument();
    expect(screen.getAllByText("590,00 TJS").length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: "Создать тариф" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Новая цена" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Согласовать" })).not.toBeInTheDocument();
  });

  it("creates a plan for an authorized manager with one stable operation id", async () => {
    authState.user.platform_capabilities = [
      "platform.billing.view",
      "platform.billing.plan.manage",
    ];
    const operationId = "123e4567-e89b-42d3-a456-426614174000";
    vi.spyOn(crypto, "randomUUID").mockReturnValue(operationId);
    createPlatformPricingPlan.mockResolvedValue({ item: PRICING_PLAN, applied: true });
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "Тарифы и цены" }));
    await screen.findByText("Бизнес");

    fireEvent.click(screen.getByRole("button", { name: "Создать тариф" }));
    fireEvent.change(screen.getByLabelText("Название"), { target: { value: "Премиум" } });
    fireEvent.change(screen.getByLabelText("Системный код"), {
      target: { value: "premium" },
    });
    fireEvent.click(
      within(screen.getByRole("dialog", { name: "Новый тариф" })).getByRole("button", {
        name: "Создать тариф",
      }),
    );

    await waitFor(() => {
      expect(createPlatformPricingPlan).toHaveBeenCalledWith({
        operation_id: operationId,
        code: "premium",
        name: "Премиум",
        description: null,
      });
    });
    expect(await screen.findByRole("status")).toHaveTextContent("Тариф создан");
  });

  it("lets only a different authorized user schedule a draft", async () => {
    authState.user.platform_capabilities = [
      "platform.billing.view",
      "platform.billing.plan.manage",
    ];
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "Тарифы и цены" }));
    await screen.findByText("Бизнес");
    expect(screen.getAllByRole("button", { name: "Согласовать" }).length).toBeGreaterThan(0);

    listPlatformPricingPlans.mockResolvedValue({
      items: [
        {
          ...PRICING_PLAN,
          versions: [{ ...PRICING_PLAN.versions[0], created_by: "developer-1" }],
        },
      ],
      total: 1,
      page: 1,
      page_size: 20,
    });
    fireEvent.click(screen.getByRole("button", { name: "Обновить" }));

    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "Согласовать" })).not.toBeInTheDocument(),
    );
    expect(screen.getAllByText("Ожидает другого согласующего").length).toBeGreaterThan(0);
  });

  it("keeps the financial account read-only without payment capabilities", async () => {
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "Клиенты и оплаты" }));

    const tenantButton = await screen.findByRole("button", { name: /Шифо Марказ/ });
    fireEvent.click(tenantButton);

    expect((await screen.findAllByText("590,00 TJS")).length).toBeGreaterThan(0);
    expect(screen.getByText("Сбалансирован")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Зарегистрировать оплату" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("Ожидают подтверждения")).not.toBeInTheDocument();
    expect(screen.queryByText("Заявки клиентов")).not.toBeInTheDocument();
    expect(listPlatformPaymentSubmissions).not.toHaveBeenCalled();
    expect(document.body.textContent).not.toContain(BILLING_TENANT.tenant_id);
  });

  it("shows the tenant submission queue only to reviewers and purges details on close", async () => {
    authState.user.platform_capabilities = [
      "platform.billing.view",
      "platform.billing.payment.review",
    ];
    listPlatformPaymentSubmissions.mockResolvedValue({
      items: [PAYMENT_SUBMISSION],
      total: 1,
      page: 1,
      page_size: 20,
    });
    getPlatformPaymentSubmission.mockResolvedValue(PAYMENT_SUBMISSION_DETAIL);
    const { queryClient } = renderPage();
    fireEvent.click(screen.getByRole("button", { name: "Клиенты и оплаты" }));
    fireEvent.click(await screen.findByRole("button", { name: /Шифо Марказ/ }));

    expect(await screen.findByText("Заявки клиентов")).toBeInTheDocument();
    expect(screen.getAllByText("•••• 9001").length).toBeGreaterThan(0);
    fireEvent.click(screen.getAllByRole("button", { name: "Проверить" })[0]!);
    expect(await screen.findByText(PRIVATE_SUBMISSION_REFERENCE)).toBeInTheDocument();
    expect(queryClient.getQueryData(PAYMENT_SUBMISSION_DETAIL_KEY)).toEqual(
      PAYMENT_SUBMISSION_DETAIL,
    );

    const dialog = screen.getByRole("dialog", { name: "Проверка заявки об оплате" });
    fireEvent.click(within(dialog).getAllByRole("button", { name: "Закрыть" })[0]!);

    await waitFor(() => {
      expect(document.body.textContent).not.toContain(PRIVATE_SUBMISSION_REFERENCE);
      expect(queryClient.getQueryData(PAYMENT_SUBMISSION_DETAIL_KEY)).toBeUndefined();
    });
  });

  it("reviews a tenant submission with a stable command and clears sensitive data", async () => {
    authState.user.platform_capabilities = [
      "platform.billing.view",
      "platform.billing.payment.review",
    ];
    listPlatformPaymentSubmissions.mockResolvedValue({
      items: [PAYMENT_SUBMISSION],
      total: 1,
      page: 1,
      page_size: 20,
    });
    getPlatformPaymentSubmission.mockResolvedValue(PAYMENT_SUBMISSION_DETAIL);
    reviewPlatformPaymentSubmission.mockResolvedValue({
      item: {
        review_id: "review-from-submission",
        tenant_id: BILLING_TENANT.tenant_id,
        target_invoice_id: FINANCIAL_INVOICE.invoice_id,
        amount: "590.00",
        currency: "TJS",
        paid_at: PAYMENT_SUBMISSION.paid_at,
        status: "pending_approval",
        row_version: 1,
        created_at: "2026-08-14T08:35:00Z",
      },
      applied: true,
    });
    vi.spyOn(crypto, "randomUUID").mockReturnValue(
      "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    );
    const { queryClient } = renderPage();
    fireEvent.click(screen.getByRole("button", { name: "Клиенты и оплаты" }));
    fireEvent.click(await screen.findByRole("button", { name: /Шифо Марказ/ }));
    fireEvent.click((await screen.findAllByRole("button", { name: "Проверить" }))[0]!);
    expect(await screen.findByText(PRIVATE_SUBMISSION_REFERENCE)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "Передать на подтверждение" }));

    await waitFor(() =>
      expect(reviewPlatformPaymentSubmission).toHaveBeenCalledWith(
        BILLING_TENANT.tenant_id,
        PAYMENT_SUBMISSION.submission_id,
        {
          operation_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
          expected_row_version: 1,
          recipient_account_key: "aurum_tjs_primary",
        },
      ),
    );
    expect(await screen.findByRole("status")).toHaveTextContent("передана другому сотруднику");
    expect(document.body.textContent).not.toContain(PRIVATE_SUBMISSION_REFERENCE);
    expect(queryClient.getQueryData(PAYMENT_SUBMISSION_DETAIL_KEY)).toBeUndefined();
  });

  it("rejects a tenant submission and clears sensitive data from DOM and cache", async () => {
    authState.user.platform_capabilities = [
      "platform.billing.view",
      "platform.billing.payment.review",
    ];
    listPlatformPaymentSubmissions.mockResolvedValue({
      items: [PAYMENT_SUBMISSION],
      total: 1,
      page: 1,
      page_size: 20,
    });
    getPlatformPaymentSubmission.mockResolvedValue(PAYMENT_SUBMISSION_DETAIL);
    rejectPlatformPaymentSubmission.mockResolvedValue({
      item: { ...PAYMENT_SUBMISSION, status: "rejected", reason_code: "amount_mismatch" },
      applied: true,
    });
    vi.spyOn(crypto, "randomUUID").mockReturnValue(
      "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    );
    const { queryClient } = renderPage();
    fireEvent.click(screen.getByRole("button", { name: "Клиенты и оплаты" }));
    fireEvent.click(await screen.findByRole("button", { name: /Шифо Марказ/ }));
    fireEvent.click((await screen.findAllByRole("button", { name: "Проверить" }))[0]!);
    expect(await screen.findByText(PRIVATE_SUBMISSION_REFERENCE)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Отклонить" }));
    fireEvent.change(screen.getByLabelText("Причина"), { target: { value: "amount_mismatch" } });
    fireEvent.click(screen.getByRole("button", { name: "Отклонить заявку" }));

    await waitFor(() =>
      expect(rejectPlatformPaymentSubmission).toHaveBeenCalledWith(
        BILLING_TENANT.tenant_id,
        PAYMENT_SUBMISSION.submission_id,
        {
          operation_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
          expected_row_version: 1,
          reason_code: "amount_mismatch",
          reason_note: null,
        },
      ),
    );
    expect(await screen.findByRole("status")).toHaveTextContent("Заявка клиента отклонена");
    expect(document.body.textContent).not.toContain(PRIVATE_SUBMISSION_REFERENCE);
    expect(queryClient.getQueryData(PAYMENT_SUBMISSION_DETAIL_KEY)).toBeUndefined();
  });

  it("registers the selected invoice amount and never leaves the bank reference in the DOM", async () => {
    authState.user.platform_capabilities = [
      "platform.billing.view",
      "platform.billing.payment.review",
    ];
    const secondInvoice = {
      ...FINANCIAL_INVOICE,
      invoice_id: "99999999-9999-4999-8999-999999999999",
      invoice_number: "AF-2026-000043",
      total_amount: "275.50",
      outstanding_amount: "275.50",
    };
    getPlatformFinancialAccount.mockResolvedValue({
      ...FINANCIAL_ACCOUNT,
      outstanding_amount: "865.50",
      invoices: [FINANCIAL_INVOICE, secondInvoice],
    });
    createPlatformBankPaymentReview.mockResolvedValue({
      item: {
        review_id: "55555555-5555-4555-8555-555555555555",
        tenant_id: BILLING_TENANT.tenant_id,
        target_invoice_id: FINANCIAL_INVOICE.invoice_id,
        amount: "590.00",
        currency: "TJS",
        paid_at: "2026-08-14T08:30:00Z",
        status: "pending_approval",
        row_version: 1,
        created_at: "2026-08-14T08:31:00Z",
      },
      applied: true,
    });
    vi.spyOn(crypto, "randomUUID").mockReturnValue("66666666-6666-4666-8666-666666666666");
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "Клиенты и оплаты" }));
    fireEvent.click(await screen.findByRole("button", { name: /Шифо Марказ/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Зарегистрировать оплату" }));

    const dialog = screen.getByRole("dialog", { name: "Регистрация банковского платежа" });
    fireEvent.change(within(dialog).getByLabelText("Счёт"), {
      target: { value: secondInvoice.invoice_id },
    });
    expect(within(dialog).getByLabelText("Сумма, TJS")).toHaveValue("275.50");

    fireEvent.change(within(dialog).getByLabelText("Сумма, TJS"), {
      target: { value: "0.00" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Передать на подтверждение" }));
    expect(await within(dialog).findByText("Сумма должна быть больше нуля")).toBeInTheDocument();
    expect(createPlatformBankPaymentReview).not.toHaveBeenCalled();

    fireEvent.change(within(dialog).getByLabelText("Сумма, TJS"), {
      target: { value: "275.50" },
    });
    const reference = "TJ-2026-PRIVATE-001";
    fireEvent.change(within(dialog).getByLabelText("Банковский номер операции"), {
      target: { value: reference },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Передать на подтверждение" }));

    await waitFor(() =>
      expect(createPlatformBankPaymentReview).toHaveBeenCalledWith(
        BILLING_TENANT.tenant_id,
        expect.objectContaining({
          operation_id: "66666666-6666-4666-8666-666666666666",
          target_invoice_id: secondInvoice.invoice_id,
          amount: "275.50",
          recipient_account_key: "aurum_tjs_primary",
          external_reference: reference,
        }),
      ),
    );
    expect(await screen.findByRole("status")).toHaveTextContent("передан другому сотруднику");
    expect(document.body.textContent).not.toContain(reference);
  });

  it("clears a bank reference when manual registration is cancelled", async () => {
    authState.user.platform_capabilities = [
      "platform.billing.view",
      "platform.billing.payment.review",
    ];
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "Клиенты и оплаты" }));
    fireEvent.click(await screen.findByRole("button", { name: /Шифо Марказ/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Зарегистрировать оплату" }));

    const reference = "TJ-PRIVATE-CANCELLED-001";
    const dialog = screen.getByRole("dialog", { name: "Регистрация банковского платежа" });
    fireEvent.change(within(dialog).getByLabelText("Банковский номер операции"), {
      target: { value: reference },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Отмена" }));
    expect(document.body.textContent).not.toContain(reference);

    fireEvent.click(await screen.findByRole("button", { name: "Зарегистрировать оплату" }));
    expect(screen.getByLabelText("Банковский номер операции")).toHaveValue("");
  });

  it("blocks own approval and lets an independent approver confirm", async () => {
    authState.user.platform_capabilities = [
      "platform.billing.view",
      "platform.billing.payment.approve",
    ];
    listPlatformPaymentApprovalQueue.mockResolvedValue({
      items: [
        {
          review_id: "review-own",
          tenant_id: BILLING_TENANT.tenant_id,
          tenant_name: BILLING_TENANT.name,
          target_invoice_id: FINANCIAL_INVOICE.invoice_id,
          invoice_number: FINANCIAL_INVOICE.invoice_number,
          amount: "100.00",
          currency: "TJS",
          paid_at: "2026-08-14T08:30:00Z",
          status: "pending_approval",
          row_version: 1,
          created_at: "2026-08-14T08:31:00Z",
          is_own_review: true,
        },
        {
          review_id: "review-other",
          tenant_id: BILLING_TENANT.tenant_id,
          tenant_name: BILLING_TENANT.name,
          target_invoice_id: FINANCIAL_INVOICE.invoice_id,
          invoice_number: FINANCIAL_INVOICE.invoice_number,
          amount: "200.00",
          currency: "TJS",
          paid_at: "2026-08-14T08:35:00Z",
          status: "pending_approval",
          row_version: 2,
          created_at: "2026-08-14T08:36:00Z",
          is_own_review: false,
        },
      ],
      total: 2,
      page: 1,
      page_size: 20,
    });
    approvePlatformBankPayment.mockResolvedValue({
      item: {
        access_restored: false,
        blocking_outstanding_amount: "390.00",
      },
      applied: true,
    });
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "Клиенты и оплаты" }));
    fireEvent.click(await screen.findByRole("button", { name: /Шифо Марказ/ }));

    expect(await screen.findByRole("button", { name: "Нужен другой сотрудник" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Проверить" }));
    expect(await screen.findByText(PRIVATE_APPROVAL_REFERENCE)).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить платёж" }));

    await waitFor(() =>
      expect(approvePlatformBankPayment).toHaveBeenCalledWith(
        BILLING_TENANT.tenant_id,
        "review-other",
        expect.objectContaining({ expected_row_version: 2 }),
      ),
    );
    expect(document.body.textContent).not.toContain(PRIVATE_APPROVAL_REFERENCE);
  });

  it("lets an independent employee reject a bank payment review", async () => {
    authState.user.platform_capabilities = [
      "platform.billing.view",
      "platform.billing.payment.approve",
    ];
    listPlatformPaymentApprovalQueue.mockResolvedValue({
      items: [
        {
          review_id: "review-reject",
          tenant_id: BILLING_TENANT.tenant_id,
          tenant_name: BILLING_TENANT.name,
          target_invoice_id: FINANCIAL_INVOICE.invoice_id,
          invoice_number: FINANCIAL_INVOICE.invoice_number,
          amount: "590.00",
          currency: "TJS",
          paid_at: "2026-08-14T08:30:00Z",
          status: "pending_approval",
          row_version: 1,
          created_at: "2026-08-14T08:31:00Z",
          is_own_review: false,
        },
      ],
      total: 1,
      page: 1,
      page_size: 20,
    });
    rejectPlatformBankPaymentReview.mockResolvedValue({ item: { status: "rejected" } });
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "Клиенты и оплаты" }));
    fireEvent.click(await screen.findByRole("button", { name: /Шифо Марказ/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Проверить" }));
    expect(await screen.findByText(PRIVATE_APPROVAL_REFERENCE)).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: "Отклонить" }));
    fireEvent.change(screen.getByLabelText("Причина"), { target: { value: "amount_mismatch" } });
    fireEvent.click(screen.getByRole("button", { name: "Отклонить платёж" }));

    await waitFor(() =>
      expect(rejectPlatformBankPaymentReview).toHaveBeenCalledWith(
        BILLING_TENANT.tenant_id,
        "review-reject",
        expect.objectContaining({
          expected_row_version: 1,
          reason_code: "amount_mismatch",
          reason_note: null,
        }),
      ),
    );
    expect(await screen.findByRole("status")).toHaveTextContent("Платёж отклонён");
    expect(document.body.textContent).not.toContain(PRIVATE_APPROVAL_REFERENCE);
  });

  it("purges protected payment details when the tab becomes hidden", async () => {
    authState.user.platform_capabilities = [
      "platform.billing.view",
      "platform.billing.payment.approve",
    ];
    listPlatformPaymentApprovalQueue.mockResolvedValue({
      items: [
        {
          ...paymentApprovalDetail("review-sensitive"),
          external_reference: undefined,
          recipient_account_key: undefined,
        },
      ],
      total: 1,
      page: 1,
      page_size: 20,
    });
    const visibility = vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
    const { queryClient } = renderPage();
    fireEvent.click(screen.getByRole("button", { name: "Клиенты и оплаты" }));
    fireEvent.click(await screen.findByRole("button", { name: /Шифо Марказ/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Проверить" }));

    expect(await screen.findByText(PRIVATE_APPROVAL_REFERENCE)).toBeInTheDocument();
    const detailKey = [
      "platform-billing",
      "v2",
      "approval-detail",
      BILLING_TENANT.tenant_id,
      "review-sensitive",
    ];
    expect(queryClient.getQueryData(detailKey)).toBeDefined();

    visibility.mockReturnValue("hidden");
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
    });

    await waitFor(() => expect(queryClient.getQueryData(detailKey)).toBeUndefined());
    expect(document.body.textContent).not.toContain(PRIVATE_APPROVAL_REFERENCE);
  });

  it("registers a bank refund request and removes its reference from the DOM", async () => {
    authState.user.platform_capabilities = [
      "platform.billing.view",
      "platform.billing.adjustment.create",
    ];
    getPlatformFinancialAccount.mockResolvedValue({
      ...FINANCIAL_ACCOUNT,
      outstanding_amount: "0.00",
      credit_balance: "110.00",
      payments: [PAYMENT],
    });
    createPlatformPaymentAdjustment.mockResolvedValue({
      item: { adjustment_id: "adjustment-1", status: "pending_approval" },
      applied: true,
    });
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "Клиенты и оплаты" }));
    fireEvent.click(await screen.findByRole("button", { name: /Шифо Марказ/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Корректировать" }));
    fireEvent.change(screen.getByLabelText("Операция"), { target: { value: "bank_refund" } });
    await screen.findByLabelText("Банковский номер");
    fireEvent.change(screen.getByLabelText("Сумма, TJS"), { target: { value: "120.00" } });
    fireEvent.change(screen.getByLabelText("Дата возврата"), {
      target: { value: "2026-08-14T10:00" },
    });
    const reference = "TJ-PRIVATE-REFUND-01";
    fireEvent.change(screen.getByLabelText("Банковский номер"), {
      target: { value: reference },
    });
    fireEvent.change(screen.getByLabelText("Обоснование"), {
      target: { value: "Возврат подтверждён банковской выпиской." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Передать на подтверждение" }));

    await waitFor(() =>
      expect(createPlatformPaymentAdjustment).toHaveBeenCalledWith(
        BILLING_TENANT.tenant_id,
        PAYMENT.payment_id,
        expect.objectContaining({
          adjustment_kind: "bank_refund",
          amount: "120.00",
          refund_reference: reference,
        }),
      ),
    );
    expect(await screen.findByRole("status")).toHaveTextContent("Запрос передан");
    expect(document.body.textContent).not.toContain(reference);
  });

  it("approves a payment adjustment only after an independent confirmation", async () => {
    authState.user.platform_capabilities = [
      "platform.billing.view",
      "platform.billing.adjustment.approve",
    ];
    getPlatformFinancialAccount.mockResolvedValue({
      ...FINANCIAL_ACCOUNT,
      payments: [{ ...PAYMENT, adjustment_pending: true }],
    });
    listPlatformPaymentAdjustmentQueue.mockResolvedValue({
      items: [
        {
          adjustment_id: "adjustment-other",
          tenant_id: BILLING_TENANT.tenant_id,
          tenant_name: BILLING_TENANT.name,
          payment_id: PAYMENT.payment_id,
          payment_amount: PAYMENT.amount,
          payment_paid_at: PAYMENT.paid_at,
          adjustment_kind: "bank_refund",
          amount: "120.00",
          currency: "TJS",
          reason_code: "bank_refund_completed",
          reason_note: "Возврат подтверждён банковской выпиской.",
          refunded_at: "2026-08-14T08:40:00Z",
          status: "pending_approval",
          row_version: 1,
          created_at: "2026-08-14T08:41:00Z",
          is_own_request: false,
        },
      ],
      total: 1,
      page: 1,
      page_size: 20,
    });
    approvePlatformPaymentAdjustment.mockResolvedValue({
      item: { access_review_required: false },
      applied: true,
    });
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "Клиенты и оплаты" }));
    fireEvent.click(await screen.findByRole("button", { name: /Шифо Марказ/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Проверить" }));
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить" }));

    await waitFor(() =>
      expect(approvePlatformPaymentAdjustment).toHaveBeenCalledWith(
        BILLING_TENANT.tenant_id,
        "adjustment-other",
        expect.objectContaining({ expected_row_version: 1 }),
      ),
    );
  });
});
