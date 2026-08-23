import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const getZReport = vi.fn();
const listShiftHistory = vi.fn();
const getSalesSummary = vi.fn();
const getSalesSummaryXlsx = vi.fn();
const getStockOnDateXlsx = vi.fn();
const getZReportXlsx = vi.fn();
const listBranches = vi.fn();
const listRegisters = vi.fn();
const getTenantSettings = vi.fn();

vi.mock("@/features/reports/api", () => ({
  getZReport: (...args: unknown[]) => getZReport(...args),
  listShiftHistory: (...args: unknown[]) => listShiftHistory(...args),
  getSalesSummary: (...args: unknown[]) => getSalesSummary(...args),
  getSalesSummaryXlsx: (...args: unknown[]) => getSalesSummaryXlsx(...args),
  getStockOnDateXlsx: (...args: unknown[]) => getStockOnDateXlsx(...args),
}));

vi.mock("@/features/pos/api", () => ({
  getZReportXlsx: (...args: unknown[]) => getZReportXlsx(...args),
}));

vi.mock("@/features/foundation/api", () => ({
  listBranches: (...args: unknown[]) => listBranches(...args),
  listRegisters: (...args: unknown[]) => listRegisters(...args),
  getTenantSettings: (...args: unknown[]) => getTenantSettings(...args),
}));

import { ReportsPage } from "@/features/reports/ReportsPage";

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ReportsPage />
    </QueryClientProvider>,
  );
}

function openReportTab(name: RegExp): void {
  fireEvent.click(screen.getByRole("tab", { name }));
}

const SHIFT = {
  id: "shift-1",
  branch_id: "branch-1",
  branch_name: "Аптека Рудаки",
  register_id: "register-1",
  register_name: "Касса 01",
  cashier_user_id: "cashier-1",
  cashier_name: "Малика Саидова",
  opened_at: "2026-05-23T08:00:00Z",
  closed_at: "2026-05-23T20:00:00Z",
  status: "closed",
  opening_cash: "100.00",
  closing_cash_actual: "1250.00",
  closing_cash_expected: "1250.00",
  closing_difference: "0.00",
  sales_total: "1200.00",
  returns_total: "50.00",
  sales_count: 12,
  returns_count: 1,
  currency: "TJS",
} as const;

const SHIFT_LIST = {
  items: [SHIFT],
  total: 1,
  page: 1,
  page_size: 25,
};

const Z_REPORT = {
  shift_id: SHIFT.id,
  opened_at: SHIFT.opened_at,
  closed_at: SHIFT.closed_at,
  register_id: SHIFT.register_id,
  cashier_user_id: SHIFT.cashier_user_id,
  opening_cash: "100.00",
  closing_cash_actual: "1250.00",
  closing_cash_expected: "1250.00",
  closing_difference: "0.00",
  totals: {
    sales_total: 1200,
    returns_total: 50,
    by_method: { cash: 700, card: 400, qr: 75, bank_transfer: 25 },
  },
  sales_count: 12,
  returns_count: 1,
};

const SALES_SUMMARY = {
  date_from: "2026-05-01",
  date_to: "2026-05-23",
  branch_name: null,
  currency: "TJS",
  gross_sales: "1200.00",
  total_discounts: "50.00",
  total_refunds: "100.00",
  net: "1050.00",
  sales_count: 12,
  returns_count: 1,
  average_sale: "95.83",
  payment_breakdown: {
    cash: "700.00",
    card: "400.00",
    qr: "75.00",
    bank_transfer: "25.00",
    mixed: "0.00",
  },
  daily: [
    {
      day: "2026-05-23",
      gross_sales: "1200.00",
      total_discounts: "50.00",
      total_refunds: "100.00",
      net: "1050.00",
      sales_count: 12,
      returns_count: 1,
    },
  ],
};

describe("ReportsPage", () => {
  beforeEach(() => {
    window.localStorage.clear();
    getZReport.mockReset();
    listShiftHistory.mockReset();
    getSalesSummary.mockReset();
    getSalesSummaryXlsx.mockReset();
    getStockOnDateXlsx.mockReset();
    getZReportXlsx.mockReset();
    listBranches.mockReset();
    listRegisters.mockReset();
    getTenantSettings.mockReset();
    listBranches.mockResolvedValue([]);
    listRegisters.mockResolvedValue([]);
    getTenantSettings.mockResolvedValue({ report_timezone: "Asia/Dushanbe" });
    listShiftHistory.mockResolvedValue(SHIFT_LIST);
    getSalesSummary.mockResolvedValue(SALES_SUMMARY);
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("shows resolved shift history and opens a Z-report without a UUID field", async () => {
    getZReport.mockResolvedValueOnce(Z_REPORT);
    renderPage();
    openReportTab(/^Смены/);

    expect(await screen.findByText("Аптека Рудаки")).toBeInTheDocument();
    expect(screen.getByText("Касса 01")).toBeInTheDocument();
    expect(screen.getByText("Малика Саидова")).toBeInTheDocument();
    expect(screen.queryByLabelText(/ID смены/i)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Открыть" }));

    await waitFor(() => {
      expect(getZReport).toHaveBeenCalledWith(SHIFT.id);
    });
    expect(await screen.findByText("На начало")).toBeInTheDocument();
    expect(screen.getAllByText("Аптека Рудаки").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Малика Саидова").length).toBeGreaterThan(0);
    expect(screen.getAllByText("QR-код").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Банковский перевод").length).toBeGreaterThan(0);
  });

  it("shows the screen sales summary with payment and daily breakdowns", async () => {
    renderPage();

    expect(await screen.findByText("Чистая выручка")).toBeInTheDocument();
    expect(screen.getAllByText("1 050,00 TJS").length).toBeGreaterThan(0);
    expect(screen.getByText("Способы оплаты")).toBeInTheDocument();
    expect(screen.getByText("Динамика по дням")).toBeInTheDocument();
    expect(getSalesSummary).toHaveBeenCalledWith(expect.objectContaining({ branch_id: undefined }));
  });

  it("blocks an oversized screen summary before sending a request", async () => {
    renderPage();
    await screen.findByText("Чистая выручка");
    expect(getSalesSummary).toHaveBeenCalledTimes(1);

    const overview = within(screen.getByRole("region", { name: "Продажи за период" }));
    fireEvent.change(overview.getByLabelText("С"), { target: { value: "2020-01-01" } });
    fireEvent.change(overview.getByLabelText("По"), { target: { value: "2026-05-23" } });
    fireEvent.click(overview.getByRole("button", { name: "Обновить сводку" }));

    expect(
      await screen.findByText("Для экранной сводки выберите не более 366 дней"),
    ).toBeInTheDocument();
    expect(getSalesSummary).toHaveBeenCalledTimes(1);
  });

  it("loads the last closed shift automatically when it is in recent history", async () => {
    window.localStorage.setItem("pos:lastClosedShiftId", SHIFT.id);
    getZReport.mockResolvedValueOnce(Z_REPORT);

    renderPage();

    await waitFor(() => {
      expect(getZReport).toHaveBeenCalledWith(SHIFT.id);
    });
    expect(await screen.findByText(/сходится/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Открыт" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Закрыть", exact: true }));
    await waitFor(() => {
      expect(screen.queryByText("На начало")).not.toBeInTheDocument();
    });
    expect(screen.getByRole("button", { name: "Открыть" })).toBeInTheDocument();
  });

  it("applies cashier search only after submitting filters", async () => {
    renderPage();
    openReportTab(/^Смены/);
    await screen.findByText("Малика Саидова");
    expect(listShiftHistory).toHaveBeenCalledTimes(1);

    fireEvent.change(screen.getByLabelText("Кассир"), {
      target: { value: "Малика" },
    });
    expect(listShiftHistory).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: "Показать" }));

    await waitFor(() => {
      expect(listShiftHistory).toHaveBeenLastCalledWith(
        expect.objectContaining({ cashier_query: "Малика" }),
      );
    });
  });

  it("renders a useful empty state", async () => {
    listShiftHistory.mockResolvedValueOnce({
      items: [],
      total: 0,
      page: 1,
      page_size: 25,
    });

    renderPage();
    openReportTab(/^Смены/);

    expect(await screen.findByText("Закрытых смен не найдено")).toBeInTheDocument();
  });

  it("loads only the active report and remembers the selected view", async () => {
    renderPage();

    await screen.findByText("Чистая выручка");
    expect(getSalesSummary).toHaveBeenCalledTimes(1);
    expect(listShiftHistory).not.toHaveBeenCalled();

    openReportTab(/^Смены/);
    expect(await screen.findByText("Малика Саидова")).toBeInTheDocument();
    expect(listShiftHistory).toHaveBeenCalledTimes(1);
    expect(window.localStorage.getItem("aurum:reports:view:v1")).toBe("shifts");

    openReportTab(/^Остатки/);
    expect(screen.getByRole("region", { name: "Остатки на дату" })).toBeInTheDocument();
    expect(window.localStorage.getItem("aurum:reports:view:v1")).toBe("stock");
  });

  it("supports keyboard navigation between report tabs", async () => {
    renderPage();
    await screen.findByText("Чистая выручка");

    const salesTab = screen.getByRole("tab", { name: /^Продажи/ });
    salesTab.focus();
    fireEvent.keyDown(salesTab, { key: "ArrowRight" });

    const shiftsTab = screen.getByRole("tab", { name: /^Смены/ });
    expect(shiftsTab).toHaveAttribute("aria-selected", "true");
    expect(await screen.findByText("Малика Саидова")).toBeInTheDocument();
  });
});
