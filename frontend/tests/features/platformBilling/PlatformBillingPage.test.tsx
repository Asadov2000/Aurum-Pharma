import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const getPlatformBillingOverview = vi.fn();
const listPlatformInvoices = vi.fn();
const listPlatformPricingPlans = vi.fn();
const createPlatformPricingPlan = vi.fn();
const createPlatformPricingPrice = vi.fn();
const schedulePlatformPricingPrice = vi.fn();
const activatePlatformPricingPrice = vi.fn();
const cancelPlatformPricingPrice = vi.fn();
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

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <PlatformBillingPage />
    </QueryClientProvider>,
  );
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
  });

  it("shows a useful read-only summary without financial mutation controls", async () => {
    renderPage();

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
    await screen.findByText("Только чтение");

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
});
