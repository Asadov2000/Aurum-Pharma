import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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

vi.mock("@/features/auth/hooks", () => ({
  useAuth: () => ({
    user: {
      is_developer: false,
      permissions: ["reports.view"],
      permission_scopes: { "reports.view": null },
    },
  }),
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
import { type StockOnDateOverview, type StockOnDateRow } from "@/features/reports/types";

const LEGACY_ROW: StockOnDateRow = {
  name: "Обычные упаковки лекарства",
  inn: null,
  branch_name: "Точка А",
  batch_number: "LEGACY-1",
  expires_at: "2026-12-31",
  qty: "1.125",
  purchase_price: "20.00",
  value: "22.50",
};
const PHYSICAL_ROW: StockOnDateRow = {
  ...LEGACY_ROW,
  stock_id: "physical-lot-1",
  inventory_kind: "physical",
  name: "Физический запас таблеток",
  batch_number: "PHYSICAL-1",
  qty: "0",
  physical_stock: {
    packaging: {
      version: 2,
      unit: "tablet",
      units_per_pack: 20,
      units_per_blister: 10,
      dispense_step: 1,
    },
    closed_packs: "3",
    opened_units: "7",
    base_units: "67",
  },
  cost_status: "known",
  purchase_price: "0.25",
  value: "16.75",
};

function stockReport(overrides: Partial<StockOnDateOverview> = {}): StockOnDateOverview {
  return {
    on_date: "2026-05-31",
    branch_name: null,
    currency: "TJS",
    rows: [LEGACY_ROW],
    total: 1,
    page: 1,
    page_size: 25,
    total_qty: "1.125",
    total_value: "22.50",
    ...overrides,
  };
}

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
    await screen.findByText("Остатков по этим условиям нет");

    const dateInput = screen.getByLabelText("Дата остатка") as HTMLInputElement;
    fireEvent.change(dateInput, { target: { value: "2026-05-31" } });
    fireEvent.click(screen.getByRole("button", { name: "Показать", exact: true }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /Скачать в Excel/i })).toBeEnabled(),
    );

    fireEvent.click(screen.getByRole("button", { name: /Скачать в Excel/i }));

    await waitFor(() => {
      expect(getStockOnDateXlsx).toHaveBeenCalledWith("2026-05-31", undefined, {
        query: undefined,
        expires_within_days: undefined,
      });
    });
    expect(downloadBlob).toHaveBeenCalledWith(blob, "stock-2026-05-31.xlsx");
  });

  it("keeps old legacy responses compatible and labels their fractional packs", async () => {
    getStockOnDate.mockResolvedValue(stockReport());
    renderCard();
    const row = within(await screen.findByRole("row", { name: /Обычные упаковки лекарства/ }));
    expect(row.getByText("1,125 уп.")).toBeInTheDocument();
    expect(row.getByText("22,50 TJS")).toBeInTheDocument();
    const summary = within(screen.getByRole("region", { name: "Итоги остатков" }));
    expect(summary.getByText("Обычные упаковки")).toBeInTheDocument();
    expect(summary.getByText("1,125 уп.")).toBeInTheDocument();
    expect(summary.queryByText("Физические единицы")).not.toBeInTheDocument();
  });

  it("separates physical counters and their summary from ordinary packs", async () => {
    getStockOnDate.mockResolvedValue(
      stockReport({
        rows: [LEGACY_ROW, PHYSICAL_ROW],
        total: 2,
        physical_quantities: [{ unit: "tablet", quantity: "67" }],
        cost_status: "known",
        known_total_value: "39.25",
        total_value: "39.25",
      }),
    );
    renderCard();
    const row = within(await screen.findByRole("row", { name: /Физический запас таблеток/ }));
    expect(row.getByText("67 табл.")).toBeInTheDocument();
    expect(row.getByText("Закрытых упаковок: 3")).toBeInTheDocument();
    expect(row.getByText("В открытых упаковках: 7 табл.")).toBeInTheDocument();
    expect(row.getByText("16,75 TJS")).toBeInTheDocument();
    expect(row.queryByText("0 уп.")).not.toBeInTheDocument();
    const summary = within(screen.getByRole("region", { name: "Итоги остатков" }));
    expect(summary.getByText("1,125 уп.")).toBeInTheDocument();
    expect(summary.getByText("67 табл.")).toBeInTheDocument();
    expect(summary.getByText("39,25 TJS")).toBeInTheDocument();
    expect(summary.queryByText("68,125")).not.toBeInTheDocument();
  });

  it("formats large physical integers exactly and keeps different units separate", async () => {
    getStockOnDate.mockResolvedValue(
      stockReport({
        rows: [
          {
            ...PHYSICAL_ROW,
            name: "Большой запас капсул",
            physical_stock: {
              packaging: {
                version: 2,
                unit: "capsule",
                units_per_pack: 20,
                units_per_blister: 10,
                dispense_step: 1,
              },
              closed_packs: "45035996273704965617",
              opened_units: "5",
              base_units: "900719925474099312345",
            },
          },
        ],
        total_qty: "0",
        physical_quantities: [
          { unit: "capsule", quantity: "900719925474099312345" },
          { unit: "ampoule", quantity: "22" },
        ],
      }),
    );
    renderCard();
    const row = within(await screen.findByRole("row", { name: /Большой запас капсул/ }));
    expect(row.getByText(/900\s719\s925\s474\s099\s312\s345 капс\./)).toBeInTheDocument();
    expect(
      row.getByText(/Закрытых упаковок: 45\s035\s996\s273\s704\s965\s617/),
    ).toBeInTheDocument();
    expect(row.getByText("В открытых упаковках: 5 капс.")).toBeInTheDocument();
    const summary = within(screen.getByRole("region", { name: "Итоги остатков" }));
    expect(summary.getByText(/900\s719\s925\s474\s099\s312\s345 капс\./)).toBeInTheDocument();
    expect(summary.getByText("22 амп.")).toBeInTheDocument();
  });

  it("marks partial valuation and shows only the known subtotal without inventing zero costs", async () => {
    getStockOnDate.mockResolvedValue(
      stockReport({
        rows: [
          LEGACY_ROW,
          { ...PHYSICAL_ROW, cost_status: "unknown", purchase_price: null, value: null },
        ],
        total: 2,
        physical_quantities: [{ unit: "tablet", quantity: "67" }],
        cost_status: "partial",
        known_total_value: "22.50",
        total_value: null,
      }),
    );
    renderCard();
    const row = within(await screen.findByRole("row", { name: /Физический запас таблеток/ }));
    expect(row.getByText("Нет данных о стоимости")).toBeInTheDocument();
    const summary = within(screen.getByRole("region", { name: "Итоги остатков" }));
    expect(summary.getByText("Известная закупочная стоимость")).toBeInTheDocument();
    expect(
      summary.getByText("Стоимость части остатков недоступна или неизвестна"),
    ).toBeInTheDocument();
    expect(summary.getByText("22,50 TJS")).toBeInTheDocument();
    expect(screen.queryByText("0,00 TJS")).not.toBeInTheDocument();
  });

  it("keeps an unavailable known subtotal unknown rather than displaying zero", async () => {
    getStockOnDate.mockResolvedValue(
      stockReport({
        rows: [{ ...PHYSICAL_ROW, cost_status: "unknown", purchase_price: null, value: null }],
        total_qty: "0",
        cost_status: "partial",
        known_total_value: null,
        total_value: null,
      }),
    );
    renderCard();
    await screen.findByRole("row", { name: /Физический запас таблеток/ });
    const summary = within(screen.getByRole("region", { name: "Итоги остатков" }));
    expect(summary.getByText("Нет данных о стоимости")).toBeInTheDocument();
    expect(screen.queryByText("0,00 TJS")).not.toBeInTheDocument();
  });

  it("renders hidden valuation from the response even when export is permitted", async () => {
    getStockOnDate.mockResolvedValue(
      stockReport({
        rows: [{ ...PHYSICAL_ROW, cost_status: "hidden", purchase_price: null, value: null }],
        total_qty: "0",
        cost_status: "hidden",
        known_total_value: null,
        total_value: null,
      }),
    );
    renderCard(true);
    const row = within(await screen.findByRole("row", { name: /Физический запас таблеток/ }));
    expect(row.getByText("Скрыто")).toBeInTheDocument();
    const summary = within(screen.getByRole("region", { name: "Итоги остатков" }));
    expect(summary.getByText("Скрыто")).toBeInTheDocument();
    expect(screen.queryByText(/TJS/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Скачать в Excel/i })).toBeEnabled();
  });

  it("distinguishes a known zero valuation from missing cost data", async () => {
    getStockOnDate.mockResolvedValue(
      stockReport({
        rows: [{ ...PHYSICAL_ROW, purchase_price: "0.00", value: "0.00" }],
        total_qty: "0",
        cost_status: "known",
        total_value: "0.00",
      }),
    );
    renderCard();
    const row = within(await screen.findByRole("row", { name: /Физический запас таблеток/ }));
    expect(row.getByText("0,00 TJS")).toBeInTheDocument();
    expect(screen.queryByText("Нет данных о стоимости")).not.toBeInTheDocument();
  });

  it("exports applied search and expiry and blocks unapplied changes", async () => {
    getStockOnDateXlsx.mockResolvedValue(new Blob(["x"]));
    renderCard(true);
    await screen.findByText("Остатков по этим условиям нет");
    fireEvent.change(screen.getByLabelText("Товар, МНН или партия"), {
      target: { value: "парацетамол" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Ещё условия" }));
    fireEvent.change(screen.getByLabelText("Срок закончится"), { target: { value: "30" } });
    fireEvent.click(screen.getByRole("button", { name: "Готово" }));
    expect(screen.getByRole("button", { name: /Скачать в Excel/i })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Показать", exact: true }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /Скачать в Excel/i })).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole("button", { name: /Скачать в Excel/i }));
    await waitFor(() =>
      expect(getStockOnDateXlsx).toHaveBeenCalledWith(expect.any(String), undefined, {
        query: "парацетамол",
        expires_within_days: 30,
      }),
    );
  });

  it("hides the previous stock totals while a different date is loading", async () => {
    renderCard();
    await screen.findByText("Остатков по этим условиям нет");
    getStockOnDate.mockImplementationOnce(() => new Promise(() => undefined));
    fireEvent.change(screen.getByLabelText("Дата остатка"), { target: { value: "2026-05-30" } });
    fireEvent.click(screen.getByRole("button", { name: "Показать", exact: true }));
    await waitFor(() => expect(getStockOnDate).toHaveBeenCalledTimes(2));
    expect(screen.queryByText("Остатков по этим условиям нет")).not.toBeInTheDocument();
    expect(screen.queryByText("Закупочная стоимость")).not.toBeInTheDocument();
  });

  it("keeps the all-branches option when no branches are available", () => {
    renderCard();
    expect(screen.getByLabelText("Аптечная точка")).toHaveValue("");
  });

  it("keeps an invalid date visible after closing the panel and prevents report submission", async () => {
    renderCard();
    await screen.findByText("Остатков по этим условиям нет");
    fireEvent.change(screen.getByLabelText("Дата остатка"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Ещё условия" }));
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
    fireEvent.click(screen.getByRole("button", { name: "Ещё условия" }));
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
