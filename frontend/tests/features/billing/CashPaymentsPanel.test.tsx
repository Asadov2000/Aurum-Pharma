import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const cashApi = vi.hoisted(() => ({
  listTenantCashPayments: vi.fn(),
  getTenantCashReceipt: vi.fn(),
  getPlatformCashReceipt: vi.fn(),
  listPlatformCashPayments: vi.fn(),
  createPlatformCashPayment: vi.fn(),
  approvePlatformCashPayment: vi.fn(),
  rejectPlatformCashPayment: vi.fn(),
}));
const printCashReceipt = vi.hoisted(() => vi.fn());

vi.mock("@/features/billing/cashApi", () => cashApi);
vi.mock("@/features/billing/cashReceiptPrint", () => ({ printCashReceipt }));

import { CashPaymentsPanel } from "@/features/billing/CashPaymentsPanel";
import { CashReceiptModal } from "@/features/billing/CashReceiptModal";
import { type CashPayment, type CashReceipt } from "@/features/billing/cashTypes";

const PAYMENT: CashPayment = {
  id: "11111111-1111-4111-8111-111111111111",
  tenant_id: "22222222-2222-4222-8222-222222222222",
  document_number: "AC-2026-0042",
  status: "confirmed",
  row_version: 2,
  amount: "600.00",
  currency: "TJS",
  paid_at: "2026-09-26T08:00:00Z",
  purpose: "Аванс за обслуживание Aurum Pharma",
  target_invoice_id: null,
  created_by: "33333333-3333-4333-8333-333333333333",
  created_at: "2026-09-26T08:01:00Z",
  confirmed_at: "2026-09-26T08:15:00Z",
  allocated_amount: "0.00",
  credit_amount: "600.00",
  can_approve: false,
  can_adjust: false,
};

const RECEIPT: CashReceipt = {
  id: PAYMENT.id,
  document_number: PAYMENT.document_number,
  tenant_id: PAYMENT.tenant_id,
  tenant_name: "Шифо Марказ",
  purpose: PAYMENT.purpose,
  amount: PAYMENT.amount,
  currency: "TJS",
  paid_at: PAYMENT.paid_at,
  confirmed_at: "2026-09-26T08:15:00Z",
  allocated_amount: "0.00",
  credit_amount: "600.00",
  allocations: [],
  title: "Подтверждение приёма наличных",
};

function renderCashView(view: React.ReactElement = <CashPaymentsPanel />) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(<QueryClientProvider client={client}>{view}</QueryClientProvider>);
}

describe("tenant cash payments and documents", () => {
  beforeEach(() => {
    Object.values(cashApi).forEach((mock) => mock.mockReset());
    printCashReceipt.mockReset();
    cashApi.listTenantCashPayments.mockResolvedValue({
      items: [PAYMENT],
      total: 1,
      limit: 20,
      offset: 0,
    });
    cashApi.getTenantCashReceipt.mockResolvedValue(RECEIPT);
    printCashReceipt.mockResolvedValue(undefined);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("lets the pharmacy read confirmed payments without registration, approval or correction actions", async () => {
    renderCashView();

    expect(await screen.findByText(PAYMENT.document_number, { exact: false })).toBeInTheDocument();
    expect(screen.getByText("600,00 TJS")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", {
        name: /подтвердить|отклонить|зарегистрировать|корректировать/i,
      }),
    ).not.toBeInTheDocument();
    expect(cashApi.createPlatformCashPayment).not.toHaveBeenCalled();
    expect(cashApi.approvePlatformCashPayment).not.toHaveBeenCalled();
    expect(cashApi.rejectPlatformCashPayment).not.toHaveBeenCalled();
    expect(document.body.textContent).not.toContain(PAYMENT.created_by);
  });

  it("opens a read-only receipt and prints only the receipt returned by the server", async () => {
    renderCashView();
    fireEvent.click(await screen.findByRole("button", { name: "Открыть документ" }));

    const dialog = await screen.findByRole("dialog", { name: RECEIPT.title });
    expect(await within(dialog).findByText(RECEIPT.tenant_name)).toBeInTheDocument();
    expect(within(dialog).getByText(RECEIPT.purpose)).toBeInTheDocument();
    expect(within(dialog).getByText(/Не является фискальным чеком/)).toBeInTheDocument();
    expect(within(dialog).queryByRole("textbox")).not.toBeInTheDocument();
    expect(
      within(dialog).queryByRole("button", { name: /подтвердить|изменить|удалить/i }),
    ).not.toBeInTheDocument();
    expect(cashApi.getTenantCashReceipt).toHaveBeenCalledWith(PAYMENT.id, expect.any(AbortSignal));
    fireEvent.click(within(dialog).getByRole("button", { name: "Печать / сохранить PDF" }));

    await waitFor(() => expect(printCashReceipt).toHaveBeenCalledWith(RECEIPT));
    expect(cashApi.getPlatformCashReceipt).not.toHaveBeenCalled();
  });

  it("does not print or show stale receipt values after a server rejection", async () => {
    cashApi.getTenantCashReceipt.mockRejectedValue(new Error("not permitted"));
    renderCashView();
    fireEvent.click(await screen.findByRole("button", { name: "Открыть документ" }));

    const dialog = await screen.findByRole("dialog", { name: RECEIPT.title });
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Не удалось загрузить подтверждение",
    );
    expect(within(dialog).getByRole("button", { name: "Печать / сохранить PDF" })).toBeDisabled();
    expect(within(dialog).queryByText(RECEIPT.tenant_name)).not.toBeInTheDocument();
    expect(printCashReceipt).not.toHaveBeenCalled();
  });

  it("reports a print failure without replacing it with success", async () => {
    printCashReceipt.mockRejectedValue(new Error("print unavailable"));
    renderCashView(<CashReceiptModal id={PAYMENT.id} onClose={vi.fn()} />);

    const print = await screen.findByRole("button", { name: "Печать / сохранить PDF" });
    await waitFor(() => expect(print).toBeEnabled());
    fireEvent.click(print);

    expect(await screen.findByRole("alert")).toHaveTextContent("Не удалось открыть печать");
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(print).toBeEnabled();
  });

  it("keeps print disabled while the authoritative receipt is loading", async () => {
    cashApi.getTenantCashReceipt.mockReturnValue(new Promise(() => undefined));
    renderCashView(<CashReceiptModal id={PAYMENT.id} onClose={vi.fn()} />);

    expect(screen.getByRole("button", { name: "Печать / сохранить PDF" })).toBeDisabled();
    expect(screen.queryByText(RECEIPT.tenant_name)).not.toBeInTheDocument();
    expect(printCashReceipt).not.toHaveBeenCalled();
  });

  it("paginates documents using the server offset", async () => {
    cashApi.listTenantCashPayments
      .mockResolvedValueOnce({ items: [PAYMENT], total: 21, limit: 20, offset: 0 })
      .mockResolvedValueOnce({
        items: [{ ...PAYMENT, id: "last-payment", document_number: "AC-2026-0062" }],
        total: 21,
        limit: 20,
        offset: 20,
      });
    renderCashView();

    fireEvent.click(await screen.findByRole("button", { name: "Вперёд" }));
    expect(await screen.findByText("AC-2026-0062", { exact: false })).toBeInTheDocument();
    expect(cashApi.listTenantCashPayments).toHaveBeenLastCalledWith(
      20,
      20,
      expect.any(AbortSignal),
    );
  });

  it("shows a list error instead of a successful empty payment state", async () => {
    cashApi.listTenantCashPayments.mockRejectedValue(new Error("offline"));
    renderCashView();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Не удалось загрузить наличные оплаты",
    );
    expect(screen.queryByText("Подтверждённых наличных оплат пока нет")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Открыть документ" })).not.toBeInTheDocument();
  });

  it("does not request a receipt until a document has been selected", () => {
    renderCashView(<CashReceiptModal id={null} onClose={vi.fn()} />);

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(cashApi.getTenantCashReceipt).not.toHaveBeenCalled();
    expect(cashApi.getPlatformCashReceipt).not.toHaveBeenCalled();
  });
});
