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
    render(<StockOnDateCard />);

    const dateInput = screen.getByLabelText("Дата остатка") as HTMLInputElement;
    fireEvent.change(dateInput, { target: { value: "2026-05-31" } });

    fireEvent.click(screen.getByRole("button", { name: /Скачать в Excel/i }));

    await waitFor(() => {
      expect(getStockOnDateXlsx).toHaveBeenCalledWith("2026-05-31", undefined);
    });
    expect(downloadBlob).toHaveBeenCalledWith(blob, "stock-2026-05-31.xlsx");
  });

  it("keeps the all-branches option when no branches are available", () => {
    render(<StockOnDateCard />);
    expect(screen.getByLabelText("Аптечная точка")).toHaveValue("");
  });
});
