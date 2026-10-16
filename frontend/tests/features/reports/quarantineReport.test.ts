import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  getQuarantineReport,
  getQuarantineReportXlsx,
  quarantineDateSchema,
  quarantineReportSchema,
} from "@/features/reports/quarantineReport";
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
const params = { date: "2026-10-16", branch_id: id(1), page: 1, page_size: 25 };
const row = {
  inventory_kind: "physical",
  source_kind: "mixed",
  intake_operation_id: id(2),
  line_id: id(3),
  stock_id: id(4),
  branch_id: id(1),
  branch_name: "Аптека",
  name: "Препарат",
  received_at: "2026-10-16T10:00:00Z",
  qty: "0",
  physical_quantity: { unit: "tablet", quantity: "2" },
  value: null,
  cost_status: "unknown",
};
const report = () => ({
  on_date: params.date,
  currency: "TJS",
  rows: [row],
  total: 1,
  page: 1,
  page_size: 25,
  total_qty: "0",
  physical_quantities: [{ unit: "tablet", quantity: "2" }],
  total_value: null,
  known_total_value: "0.00",
  cost_status: "partial",
});
beforeEach(() => {
  mocks.get.mockReset();
  mocks.active = true;
});
describe("quarantine report contracts", () => {
  it("reads selected date, branch and page with exact validated response", async () => {
    mocks.get.mockResolvedValue({ data: report() });
    expect(await getQuarantineReport(params)).toEqual(report());
    expect(mocks.get).toHaveBeenCalledWith("/reports/quarantine-on-date", {
      params,
      signal: undefined,
    });
  });
  it("exports all rows for the applied filters without page limits", async () => {
    const blob = new Blob(["xlsx"]);
    mocks.get.mockResolvedValue({ data: blob });
    expect(await getQuarantineReportXlsx(params)).toBe(blob);
    expect(mocks.get).toHaveBeenCalledWith("/reports/quarantine-on-date.xlsx", {
      params: { date: params.date, branch_id: id(1) },
      responseType: "blob",
    });
  });
  it.each(["2026-02-30", "bad", "2026-13-01"])("rejects invalid date %s", (value) =>
    expect(quarantineDateSchema.safeParse(value).success).toBe(false),
  );
  it.each([{ on_date: "2026-10-15" }, { page: 2 }, { rows: [{ ...row, branch_id: id(9) }] }])(
    "rejects mismatching report filters",
    async (patch) => {
      mocks.get.mockResolvedValue({ data: { ...report(), ...patch } });
      await expect(getQuarantineReport(params)).rejects.toThrow();
    },
  );
  it("rejects cost disclosure on hidden rows and misleading physical pack totals", () => {
    for (const patch of [
      { cost_status: "hidden", value: "10.00" },
      { qty: "2" },
      { physical_quantity: null },
      { comment: "private" },
    ])
      expect(
        quarantineReportSchema.safeParse({ ...report(), rows: [{ ...row, ...patch }] }).success,
      ).toBe(false);
  });
  it("rejects stale account responses including export", async () => {
    mocks.get.mockImplementation(async () => {
      mocks.active = false;
      return { data: report() };
    });
    await expect(getQuarantineReport(params)).rejects.toThrow("Аккаунт изменился");
    mocks.active = true;
    await expect(getQuarantineReportXlsx(params)).rejects.toThrow("Аккаунт изменился");
  });
  it("never presents partial or hidden value as a complete zero", () => {
    expect(quarantineReportSchema.safeParse({ ...report(), total_value: "0.00" }).success).toBe(
      false,
    );
    expect(
      quarantineReportSchema.safeParse({
        ...report(),
        cost_status: "hidden",
        known_total_value: "0.00",
      }).success,
    ).toBe(false);
  });
});
