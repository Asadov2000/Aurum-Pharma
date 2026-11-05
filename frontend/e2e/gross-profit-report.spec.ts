import { request, test, type APIRequestContext } from "@playwright/test";
import {
  apiContext,
  apiLogin,
  clearLoginRateLimit,
  expect,
  loginInBrowser,
  OWNER,
  seedAcceptedBatch,
  seedBranch,
  seedCatalogItem,
  seedRegister,
  seedSupplier,
  uniqueName,
  type SeededCatalogItem,
} from "./helpers";

interface Scenario {
  kind: "physical" | "mixed";
  version: 3 | 4;
  paid: string;
  zero?: boolean;
}
interface CompletedSale {
  schema_version: number;
  sale_id: string;
  shift_id: string;
  completed_at: string;
  total_amount: string;
  lines: {
    inventory_kind: string;
    gross_amount: string;
    discount_amount: string;
    net_amount: string;
  }[];
}

async function receivePhysicalStock(
  api: APIRequestContext,
  branchId: string,
  catalogId: string,
  expiresAt: string,
): Promise<void> {
  const supplier = await seedSupplier(api, uniqueName("Report unit supplier"));
  const created = await api.post("incoming", {
    data: {
      operation_id: crypto.randomUUID(),
      branch_id: branchId,
      supplier_id: supplier.id,
      document_date: new Date().toISOString().slice(0, 10),
    },
  });
  expect(created.ok()).toBe(true);
  const document = (await created.json()) as { id: string };
  const added = await api.post(`incoming/${document.id}/items`, {
    data: {
      operation_id: crypto.randomUUID(),
      catalog_id: catalogId,
      expires_at: expiresAt,
      qty: "4",
      purchase_price: "5.00",
      sale_price: "10.00",
    },
  });
  expect(added.ok()).toBe(true);
  const item = (await added.json()) as { id: string };
  expect(
    (
      await api.patch(`incoming/${document.id}/items/${item.id}`, {
        data: { prepare_unit_sale: true },
      })
    ).ok(),
  ).toBe(true);
  const checked = await api.get(`incoming/${document.id}`);
  expect(checked.ok()).toBe(true);
  const preview = (await checked.json()) as { acceptance_revision: string };
  expect(
    (
      await api.post(`incoming/${document.id}/accept`, {
        data: { stock_mode: "physical_unit_v2", expected_revision: preview.acceptance_revision },
      })
    ).ok(),
  ).toBe(true);
}

async function seedSale(api: APIRequestContext, scenario: Scenario) {
  const branch = await seedBranch(api, uniqueName(`Report ${scenario.kind} branch`));
  const register = await seedRegister(api, branch.id, uniqueName("Report discount register"));
  const physical = await seedCatalogItem(api, uniqueName("Report discount tablets"), "10.00");
  const expiry = new Date();
  expiry.setUTCDate(expiry.getUTCDate() + 180);
  const expiresAt = expiry.toISOString().slice(0, 10);
  expect(
    (
      await api.patch(`catalog/${physical.id}`, {
        data: {
          unit_sale_preparation: {
            version: 1,
            state: "prepared",
            unit: "tablet",
            units_per_pack: 20,
            units_per_blister: 10,
            dispense_step: 1,
            unit_price: "0.50",
          },
        },
      })
    ).ok(),
  ).toBe(true);
  await receivePhysicalStock(api, branch.id, physical.id, expiresAt);
  expect(
    (
      await api.post("shifts/open", {
        data: { register_id: register.id, opening_cash: "100.00" },
      })
    ).ok(),
  ).toBe(true);
  const created = await api.post("sales", {
    data: { operation_id: crypto.randomUUID(), register_id: register.id },
  });
  expect(created.ok()).toBe(true);
  const draft = (await created.json()) as { id: string };
  let legacy: SeededCatalogItem | undefined;
  if (scenario.kind === "mixed") {
    const supplier = await seedSupplier(api, uniqueName("Report pack supplier"));
    legacy = await seedCatalogItem(api, uniqueName("Report ordinary pack"), "10.00");
    await seedAcceptedBatch(api, {
      branchId: branch.id,
      supplierId: supplier.id,
      catalogId: legacy.id,
      qty: "4",
      purchasePrice: "5.00",
      salePrice: "10.00",
      expiresAt,
    });
    // The public ordinary-pack API has no discount input; backend tests cover it.
    expect(
      (
        await api.post(`sales/${draft.id}/items`, {
          data: { operation_id: crypto.randomUUID(), catalog_id: legacy.id, qty: "1.000" },
        })
      ).ok(),
    ).toBe(true);
  }
  expect(
    (
      await api.put(`sales/${draft.id}/physical-cart/items/${crypto.randomUUID()}`, {
        data: {
          catalog_id: physical.id,
          position: scenario.kind === "mixed" ? 2 : 1,
          sale_format: "pack",
          quantity: 1,
          discount_amount: scenario.zero ? "9.99" : "1.23",
        },
      })
    ).ok(),
  ).toBe(true);
  const quoted = await api.get(`sales/${draft.id}/physical-cart/quote`);
  expect(quoted.ok()).toBe(true);
  const quote = (await quoted.json()) as {
    revision_hash: string;
    quote: { total_amount: string };
  };
  expect(quote.quote.total_amount).toBe(scenario.paid);
  const confirmation = {
    operation_id: crypto.randomUUID(),
    sale_id: draft.id,
    expected_revision_hash: quote.revision_hash,
    expected_total_amount: scenario.paid,
    cash_tendered: scenario.paid,
  };
  const prefix = `sales/${draft.id}/${scenario.kind}-cash`;
  expect((await api.post(`${prefix}/intent`, { data: confirmation })).ok()).toBe(true);
  const paid = await api.post(`${prefix}/complete`, { data: confirmation });
  expect(paid.ok()).toBe(true);
  const sale = (await paid.json()) as CompletedSale;
  expect(sale).toMatchObject({ schema_version: scenario.version, total_amount: scenario.paid });
  expect(sale.lines.find((line) => line.inventory_kind === "physical_unit")).toMatchObject({
    gross_amount: "10.00",
    discount_amount: scenario.zero ? "9.99" : "1.23",
    net_amount: scenario.zero ? "0.01" : "8.77",
  });
  return { branch, register, sale, physical, legacy };
}
interface ProfitReport {
  sales_revenue: string;
  refunds: string;
  net_revenue: string;
  net_sales_cost: string | null;
  known_net_sales_cost: string | null;
  gross_profit: string | null;
  known_gross_profit: string | null;
  writeoff_cost: string | null;
  cost_status: string;
  total: number;
  rows: { event_kind: string; branch_id: string }[];
}
async function returnUnits(
  api: APIRequestContext,
  scenario: Scenario,
  sale: CompletedSale,
  quantity: number,
  expected: string,
): Promise<void> {
  const prefix = `sales/${sale.sale_id}/${scenario.kind}-cash-refunds`;
  const response = await api.get(`${prefix}/options`);
  expect(response.ok()).toBe(true);
  const options = (await response.json()) as {
    lines: { inventory_kind?: string; line_id: string }[];
  };
  const line = options.lines.find(
    (item) => scenario.kind === "physical" || item.inventory_kind === "physical_unit",
  );
  if (!line) throw new Error("Missing physical refund source");
  const selection = { line_id: line.line_id, sale_format: "unit", quantity };
  const items = [
    scenario.kind === "mixed" ? { ...selection, inventory_kind: "physical_unit" } : selection,
  ];
  const quoted = await api.post(`${prefix}/quote`, { data: { items } });
  expect(quoted.ok()).toBe(true);
  const quote = (await quoted.json()) as { total_amount: string; revision_hash: string };
  expect(quote.total_amount).toBe(expected);
  const zero = expected === "0.00";
  const result = await api.post(
    zero ? `sales/${sale.sale_id}/${scenario.kind}-zero-returns/complete` : `${prefix}/complete`,
    {
      data: {
        operation_id: crypto.randomUUID(),
        items,
        expected_revision_hash: quote.revision_hash,
        reason_code: "quality_issue",
        comment: "E2E gross profit historical return",
        ...(!zero ? { shift_id: sale.shift_id, expected_total_amount: expected } : {}),
      },
    },
  );
  expect(result.ok()).toBe(true);
}
for (const scenario of [
  { kind: "physical", version: 3, paid: "8.77" },
  { kind: "mixed", version: 4, paid: "18.77" },
  { kind: "physical", version: 3, paid: "0.01", zero: true },
] satisfies Scenario[]) {
  test(`gross profit ${scenario.kind} ${scenario.zero ? "zero return" : "discount and refund"} reconciles historical costs and scoped XLSX`, async ({
    page,
  }) => {
    test.setTimeout(180_000);
    clearLoginRateLimit(OWNER.email);
    const anonymous = await request.newContext();
    const tokens = await apiLogin(anonymous, OWNER);
    const api = await apiContext(tokens.access_token);
    try {
      const fixture = await seedSale(api, scenario);
      await returnUnits(api, scenario, fixture.sale, 1, scenario.zero ? "0.00" : "0.43");
      const emptyBranch = await seedBranch(api, uniqueName("Empty profit branch"));
      const settingsResponse = await api.get("tenant/operational-settings");
      expect(settingsResponse.ok()).toBe(true);
      const settings = (await settingsResponse.json()) as { report_timezone: string };
      const day = (instant: string) => {
        const parts = new Intl.DateTimeFormat("en-CA", {
          timeZone: settings.report_timezone,
          year: "numeric",
          month: "2-digit",
          day: "2-digit",
        }).formatToParts(new Date(instant));
        return ["year", "month", "day"]
          .map((part) => parts.find((value) => value.type === part)?.value)
          .join("-");
      };
      const from = day(fixture.sale.completed_at),
        to = day(new Date().toISOString());
      const params = { date_from: from, date_to: to, branch_id: fixture.branch.id };
      const read = async () => {
        const response = await api.get("reports/gross-profit", { params });
        expect(response.ok()).toBe(true);
        return (await response.json()) as ProfitReport;
      };
      const report = await read();
      const salesSummary = await api.get("reports/sales-summary", {
        params: { from, to, branch_id: fixture.branch.id },
      });
      expect(salesSummary.ok()).toBe(true);
      expect(await salesSummary.json()).toMatchObject({ net: report.net_revenue });
      expect(report).toMatchObject({
        sales_revenue: scenario.paid,
        refunds: scenario.zero ? expect.stringMatching(/^-?0(?:\.0+)?$/) : "0.43",
        net_revenue: scenario.zero ? "0.01" : scenario.kind === "mixed" ? "18.34" : "8.34",
        known_net_sales_cost: "4.75",
        net_sales_cost: scenario.kind === "mixed" ? null : "4.75",
        gross_profit: scenario.kind === "mixed" ? null : scenario.zero ? "-4.74" : "3.59",
        known_gross_profit: scenario.zero ? "-4.74" : "3.59",
        cost_status: scenario.kind === "mixed" ? "partial" : "known",
        writeoff_cost: expect.stringMatching(/^0(?:\.0+)?$/),
      });
      expect(report.rows.every((row) => row.branch_id === fixture.branch.id)).toBe(true);
      await page.setViewportSize({ width: 1366, height: 900 });
      await loginInBrowser(page, OWNER);
      await page.goto("/reports");
      await page.getByRole("tab", { name: /^Прибыль(?:\s|$)/ }).click();
      const panel = page.getByRole("region", { name: "Валовая прибыль", exact: true });
      await panel.getByLabel("Начало периода", { exact: true }).fill(from);
      await panel.getByLabel("Конец периода", { exact: true }).fill(to);
      await panel.getByLabel("Аптечная точка", { exact: true }).selectOption(fixture.branch.id);
      const matches = (url: URL, path: string) =>
        url.pathname.endsWith(path) &&
        url.searchParams.get("branch_id") === fixture.branch.id &&
        url.searchParams.get("date_from") === from &&
        url.searchParams.get("date_to") === to;
      const loaded = page.waitForResponse((response) =>
        matches(new URL(response.url()), "/reports/gross-profit"),
      );
      await panel.getByRole("button", { name: "Показать", exact: true }).click();
      expect(await (await loaded).json()).toMatchObject({
        net_revenue: report.net_revenue,
        gross_profit: report.gross_profit,
        cost_status: report.cost_status,
      });
      await expect(
        panel.getByRole("table", { name: "Операции валовой прибыли", exact: true }),
      ).toBeVisible();
      const exporting = page.waitForResponse((response) =>
        matches(new URL(response.url()), "/reports/gross-profit.xlsx"),
      );
      const downloaded = page.waitForEvent("download");
      await panel.getByRole("button", { name: "Скачать в Excel", exact: true }).click();
      const workbook = await exporting;
      expect(workbook.ok()).toBe(true);
      expect(workbook.headers()["content-type"]).toContain(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      );
      expect((await workbook.body()).subarray(0, 2).toString()).toBe("PK");
      expect((await downloaded).suggestedFilename()).toMatch(/\.xlsx$/);
      if (!scenario.zero && scenario.kind === "physical") {
        await returnUnits(api, scenario, fixture.sale, 19, "8.34");
        expect(await read()).toMatchObject({
          sales_revenue: "8.77",
          refunds: "8.77",
          net_revenue: expect.stringMatching(/^0(?:\.0+)?$/),
          net_sales_cost: "0.00",
          gross_profit: "0.00",
        });
      }
      const empty = await api.get("reports/gross-profit", {
        params: { ...params, branch_id: emptyBranch.id },
      });
      expect(empty.ok()).toBe(true);
      expect(await empty.json()).toMatchObject({
        total: 0,
        rows: [],
        net_revenue: expect.stringMatching(/^0(?:\.0+)?$/),
      });
      const closed = await api.post(`shifts/${fixture.sale.shift_id}/close`, {
        data: {
          closing_cash_actual: scenario.zero
            ? "100.01"
            : scenario.kind === "mixed"
              ? "118.34"
              : "100.00",
        },
      });
      expect(closed.ok()).toBe(true);
      const zResponse = await api.get(`shifts/${fixture.sale.shift_id}/z-report`);
      expect(zResponse.ok()).toBe(true);
      const z = (await zResponse.json()) as {
        totals: { sales_total: string; discounts_total: string; returns_total: string };
      };
      const finalReport = await read();
      const cents = (value: string) => Math.round(Number(value) * 100);
      expect(
        cents(z.totals.sales_total) -
          cents(z.totals.discounts_total) -
          cents(z.totals.returns_total),
      ).toBe(cents(finalReport.net_revenue));
    } finally {
      await api.dispose();
      await anonymous.dispose();
    }
  });
}
