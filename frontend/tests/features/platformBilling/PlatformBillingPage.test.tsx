import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const getPlatformBillingOverview = vi.fn();
const listPlatformInvoices = vi.fn();

vi.mock("@/features/platformBilling/api", () => ({
  getPlatformBillingOverview: (...args: unknown[]) => getPlatformBillingOverview(...args),
  listPlatformInvoices: (...args: unknown[]) => listPlatformInvoices(...args),
}));

vi.mock("@/features/auth/hooks", () => ({
  useAuth: () => ({
    user: {
      id: "developer-1",
      is_developer: true,
      is_administrator: false,
      platform_capabilities: ["platform.billing.view"],
    },
  }),
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
    getPlatformBillingOverview.mockResolvedValue(OVERVIEW);
    listPlatformInvoices.mockResolvedValue({
      items: [INVOICE],
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
});
