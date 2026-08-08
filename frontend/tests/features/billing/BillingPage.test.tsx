import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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
  status: "open" as const,
  paid_at: null,
  notes: null,
};

describe("BillingPage", () => {
  beforeEach(() => {
    getCurrentSubscription.mockReset();
    listInvoices.mockReset();
    getInvoice.mockReset();
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("shows a hint when there is no subscription yet", async () => {
    getCurrentSubscription.mockResolvedValueOnce(null);
    listInvoices.mockResolvedValueOnce([]);
    renderPage();
    expect(
      await screen.findByText(/Подписки пока нет\. Свяжитесь с поддержкой/i),
    ).toBeInTheDocument();
    expect(await screen.findByText(/Счетов пока нет/i)).toBeInTheDocument();
  });

  it("renders subscription card with plan name + invoice row", async () => {
    getCurrentSubscription.mockResolvedValueOnce(SUB);
    listInvoices.mockResolvedValueOnce([INV]);
    renderPage();
    expect(await screen.findByText("Aurum Pharma")).toBeInTheDocument();
    expect(screen.getAllByText("INV-2026-001")).not.toHaveLength(0);
    expect(screen.getByText(/Активна/i)).toBeInTheDocument();
    expect(screen.getAllByText(/Открыт/i)).not.toHaveLength(0);
  });

  it("opens the invoice detail modal on row click", async () => {
    getCurrentSubscription.mockResolvedValueOnce(SUB);
    listInvoices.mockResolvedValueOnce([INV]);
    getInvoice.mockResolvedValueOnce({ ...INV, payments: [] });
    renderPage();
    const invoiceButtons = await screen.findAllByRole("button", {
      name: "Открыть счёт INV-2026-001",
    });
    fireEvent.click(invoiceButtons[0]!);
    expect(await screen.findByText(/Счёт № INV-2026-001/i)).toBeInTheDocument();
    expect(getInvoice).toHaveBeenCalledWith(INV.id);
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
});
