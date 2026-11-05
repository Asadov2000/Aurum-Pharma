import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  getGrossProfitReport,
  getGrossProfitReportXlsx,
  grossProfitReportSchema,
  grossProfitPeriodSchema,
} from "@/features/reports/grossProfitReport";
const mocks = vi.hoisted(() => ({ get: vi.fn(), active: true }));
vi.mock("@/lib/api", () => ({
  api: { get: mocks.get },
  withAuthSession: (config: unknown) => config,
}));
vi.mock("@/features/pos/posStorageScope", () => ({
  currentPosStorageScope: () => ({ tenantId: "tenant", userId: "user" }),
  capturePosSessionGuard: () => () => mocks.active,
}));
const id = (n: number) => `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
const params = {
  date_from: "2026-10-01",
  date_to: "2026-10-16",
  branch_id: id(1),
  page: 1,
  page_size: 25,
};
const row = {
  event_id: id(2),
  line_id: id(3),
  branch_id: id(1),
  branch_name: "Аптека",
  name: "Препарат",
  occurred_at: "2026-10-16T10:00:00Z",
  source_kind: "physical",
  event_kind: "refund",
  revenue: "-0.43",
  cost: "-0.25",
  gross_profit: "-0.18",
  cost_status: "known",
};
const report = () => ({
  date_from: params.date_from,
  date_to: params.date_to,
  currency: "TJS",
  rows: [row],
  total: 1,
  page: 1,
  page_size: 25,
  sales_revenue: "8.77",
  refunds: "0.43",
  net_revenue: "8.34",
  net_sales_cost: "4.75",
  known_net_sales_cost: "4.75",
  gross_profit: "3.59",
  known_gross_profit: "3.59",
  writeoff_cost: "0.00",
  known_writeoff_cost: "0.00",
  cost_status: "known",
  writeoff_cost_status: "known",
});
beforeEach(() => {
  mocks.get.mockReset();
  mocks.active = true;
});
describe("gross profit contracts", () => {
  it("accepts signed refund revenue and cost and known zero write-offs", async () => {
    mocks.get.mockResolvedValue({ data: report() });
    expect(await getGrossProfitReport(params)).toEqual(report());
  });
  it("exports the entire applied date range without pagination", async () => {
    const blob = new Blob(["xlsx"]);
    mocks.get.mockResolvedValue({ data: blob });
    expect(await getGrossProfitReportXlsx(params)).toBe(blob);
    expect(mocks.get).toHaveBeenCalledWith("/reports/gross-profit.xlsx", {
      params: { date_from: params.date_from, date_to: params.date_to, branch_id: id(1) },
      responseType: "blob",
    });
  });
  it.each([
    { date_from: "2026-02-30", date_to: "2026-03-01" },
    { date_from: "2026-10-17", date_to: "2026-10-16" },
  ])("rejects invalid local date ranges", (period) =>
    expect(grossProfitPeriodSchema.safeParse(period).success).toBe(false),
  );
  it.each([
    { date_from: "2026-10-02" },
    { date_to: "2026-10-15" },
    { page: 2 },
    { rows: [{ ...row, branch_id: id(9) }] },
  ])("rejects mismatching report filters", async (patch) => {
    mocks.get.mockResolvedValue({ data: { ...report(), ...patch } });
    await expect(getGrossProfitReport(params)).rejects.toThrow();
  });
  it("accepts unknown mixed subtotal without fabricating full profit", () => {
    expect(
      grossProfitReportSchema.parse({
        ...report(),
        rows: [
          { ...row, source_kind: "mixed", cost_status: "unknown", cost: null, gross_profit: null },
        ],
        net_sales_cost: null,
        gross_profit: null,
        cost_status: "partial",
      }).gross_profit,
    ).toBeNull();
  });
  it("rejects exposed costs under hidden status and partial full profit", () => {
    for (const patch of [
      { cost_status: "hidden" },
      { cost_status: "partial" },
      { rows: [{ ...row, cost_status: "hidden" }] },
    ])
      expect(grossProfitReportSchema.safeParse({ ...report(), ...patch }).success).toBe(false);
  });
  it("keeps write-offs separate from gross profit", () => {
    const writeoff = {
      ...row,
      line_id: null,
      event_kind: "writeoff",
      revenue: "0.00",
      cost: "5.00",
      gross_profit: null,
    };
    expect(grossProfitReportSchema.safeParse({ ...report(), rows: [writeoff] }).success).toBe(true);
    expect(
      grossProfitReportSchema.safeParse({
        ...report(),
        rows: [{ ...writeoff, gross_profit: "-5.00" }],
      }).success,
    ).toBe(false);
  });
  it("rejects stale account JSON and XLSX responses", async () => {
    mocks.get.mockImplementation(async () => {
      mocks.active = false;
      return { data: report() };
    });
    await expect(getGrossProfitReport(params)).rejects.toThrow("Аккаунт изменился");
    mocks.active = true;
    await expect(getGrossProfitReportXlsx(params)).rejects.toThrow("Аккаунт изменился");
  });
});

it("accepts 366 inclusive days but rejects 367", () => {
  expect(
    grossProfitPeriodSchema.safeParse({ date_from: "2024-01-01", date_to: "2024-12-31" }).success,
  ).toBe(true);
  expect(
    grossProfitPeriodSchema.safeParse({ date_from: "2024-01-01", date_to: "2025-01-01" }).success,
  ).toBe(false);
});
