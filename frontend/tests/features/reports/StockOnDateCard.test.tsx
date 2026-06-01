import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const getStockOnDateXlsx = vi.fn();
const downloadBlob = vi.fn();

vi.mock("@/features/reports/api", () => ({
  getZReport: vi.fn(),
  getSalesSummaryXlsx: vi.fn(),
  getStockOnDateXlsx: (...a: unknown[]) => getStockOnDateXlsx(...a),
}));

vi.mock("@/lib/download", () => ({
  downloadBlob: (...a: unknown[]) => downloadBlob(...a),
}));

vi.mock("@/features/foundation/queries", () => ({
  useBranchesQuery: () => ({ data: [] }),
}));

import { StockOnDateCard } from "@/features/reports/ReportsPage";

describe("StockOnDateCard", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("downloads the stock XLSX for the selected date", async () => {
    const blob = new Blob(["x"]);
    getStockOnDateXlsx.mockResolvedValueOnce(blob);
    render(<StockOnDateCard />);

    const dateInput = screen.getByLabelText("Дата") as HTMLInputElement;
    fireEvent.change(dateInput, { target: { value: "2026-05-31" } });

    fireEvent.click(screen.getByRole("button", { name: /Скачать отчёт по остаткам/i }));

    await waitFor(() => {
      expect(getStockOnDateXlsx).toHaveBeenCalledWith("2026-05-31", undefined);
    });
    expect(downloadBlob).toHaveBeenCalledWith(blob, "stock-2026-05-31.xlsx");
  });

  it("hides the branch select when no branches are available", () => {
    render(<StockOnDateCard />);
    expect(screen.queryByLabelText("Филиал")).not.toBeInTheDocument();
  });
});
