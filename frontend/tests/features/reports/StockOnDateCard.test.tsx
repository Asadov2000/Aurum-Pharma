import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const getStockOnDateXlsx = vi.fn();
const getStockOnDate = vi.fn();
const downloadBlob = vi.fn();

vi.mock("@/features/reports/api", () => ({
  getZReport: vi.fn(),
  listShiftHistory: vi.fn(),
  getSalesSummary: vi.fn(),
  getSalesSummaryXlsx: vi.fn(),
  getStockOnDateXlsx: (...a: unknown[]) => getStockOnDateXlsx(...a),
  getStockOnDate: (...a: unknown[]) => getStockOnDate(...a),
  getTopProducts: vi.fn(),
}));

vi.mock("@/lib/download", () => ({
  downloadBlob: (...a: unknown[]) => downloadBlob(...a),
}));

vi.mock("@/features/foundation/queries", () => ({
  useBranchesQuery: () => ({ data: [] }),
  useRegistersQuery: () => ({ data: [] }),
  useTenantOperationalSettingsQuery: () => ({
    data: { report_timezone: "Asia/Dushanbe" },
  }),
}));

import { StockOnDateCard } from "@/features/reports/ReportsPage";

function renderCard(canExport = false) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <StockOnDateCard canExport={canExport} />
    </QueryClientProvider>,
  );
}

describe("StockOnDateCard", () => {
  beforeEach(() => {
    getStockOnDate.mockResolvedValue({
      on_date: "2026-05-31",
      branch_name: null,
      currency: "TJS",
      rows: [],
      total: 0,
      page: 1,
      page_size: 25,
      total_qty: "0",
      total_value: "0.00",
    });
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("downloads the stock XLSX for the selected date", async () => {
    const blob = new Blob(["x"]);
    getStockOnDateXlsx.mockResolvedValueOnce(blob);
    renderCard(true);

    const dateInput = screen.getByLabelText("Дата остатка") as HTMLInputElement;
    fireEvent.change(dateInput, { target: { value: "2026-05-31" } });

    fireEvent.click(screen.getByRole("button", { name: /Скачать в Excel/i }));

    await waitFor(() => {
      expect(getStockOnDateXlsx).toHaveBeenCalledWith("2026-05-31", undefined);
    });
    expect(downloadBlob).toHaveBeenCalledWith(blob, "stock-2026-05-31.xlsx");
  });

  it("keeps the all-branches option when no branches are available", () => {
    renderCard();
    fireEvent.click(screen.getByRole("button", { name: /^Фильтры/ }));
    expect(screen.getByLabelText("Аптечная точка")).toHaveValue("");
  });

  it("keeps an invalid date visible after closing the panel and prevents report submission", async () => {
    renderCard();
    await screen.findByText("Остатков по этим условиям нет");
    fireEvent.change(screen.getByLabelText("Дата остатка"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: /^Фильтры/ }));
    fireEvent.click(screen.getByRole("button", { name: "Готово" }));
    fireEvent.click(screen.getByRole("button", { name: "Показать", exact: true }));

    expect(await screen.findByText("Укажите дату")).toBeVisible();
    expect(screen.getByLabelText("Дата остатка")).toBeVisible();
    expect(getStockOnDate).toHaveBeenCalledTimes(1);
  });

  it("does not expose XLSX export without the export permission", () => {
    renderCard();
    expect(screen.queryByRole("button", { name: /Скачать в Excel/i })).not.toBeInTheDocument();
  });

  it("retains panel values until explicit report submission and resets an applied expiry", async () => {
    renderCard();
    await screen.findByText("Остатков по этим условиям нет");
    expect(getStockOnDate).toHaveBeenCalledTimes(1);

    fireEvent.change(screen.getByLabelText("Дата остатка"), { target: { value: "2026-05-31" } });
    fireEvent.click(screen.getByRole("button", { name: /^Фильтры/ }));
    fireEvent.change(screen.getByLabelText("Срок закончится"), { target: { value: "30" } });
    fireEvent.click(screen.getByRole("button", { name: "Готово" }));
    expect(getStockOnDate).toHaveBeenCalledTimes(1);
    expect(screen.getByText(/Условия изменены/)).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "Показать", exact: true }));
    await waitFor(() =>
      expect(getStockOnDate).toHaveBeenLastCalledWith(
        expect.objectContaining({ date: "2026-05-31", expires_within_days: 30, page: 1 }),
      ),
    );
    expect(screen.queryByText(/Условия изменены/)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Сбросить фильтр «Срок закончится»" }));
    await waitFor(() =>
      expect(getStockOnDate).toHaveBeenLastCalledWith(
        expect.objectContaining({ date: "2026-05-31", expires_within_days: undefined, page: 1 }),
      ),
    );
  });
});
