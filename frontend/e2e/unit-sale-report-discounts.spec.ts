import { request, test, type APIRequestContext, type Page, type Response } from "@playwright/test";
import { readFile } from "node:fs/promises";
import { inflateRawSync } from "node:zlib";

import { type MeResponse } from "../src/features/auth/types";
import {
  type SalesSummaryOverview,
  type StockOnDateOverview,
  type TopProductsOverview,
} from "../src/features/reports/types";

import {
  apiContext,
  apiLogin,
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

const XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
const cases = [
  { kind: "physical", version: 3, gross: "10.00", paid: "8.77", net: "8.34", cash: "108.34" },
  { kind: "mixed", version: 4, gross: "20.00", paid: "18.77", net: "18.34", cash: "118.34" },
] as const;
type Scenario = (typeof cases)[number];

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
          discount_amount: "1.23",
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
    discount_amount: "1.23",
    net_amount: "8.77",
  });
  return { branch, register, sale, physical, legacy };
}

type ReportFixture = Awaited<ReturnType<typeof seedSale>>;

function assertTopProducts(
  report: TopProductsOverview,
  fixture: ReportFixture,
  quantityOnly: boolean,
): void {
  expect(report.rows).toHaveLength(quantityOnly || !fixture.legacy ? 1 : 2);
  expect(report).toMatchObject({
    sort_by: quantityOnly ? "quantity" : "revenue",
    quantity_basis: quantityOnly ? "tablet" : "legacy_pack",
  });
  const physical = report.rows.find((row) => row.catalog_id === fixture.physical.id);
  expect(physical).toMatchObject({
    name: fixture.physical.brand_name,
    physical_quantities: [{ unit: "tablet", quantity: "19" }],
    revenue: "8.34",
    receipts_count: 1,
  });
  expect(physical?.quantity).toMatch(/^0(?:\.0+)?$/);
  if (!quantityOnly && fixture.legacy) {
    const legacy = report.rows.find((row) => row.catalog_id === fixture.legacy?.id);
    expect(legacy).toMatchObject({
      name: fixture.legacy.brand_name,
      physical_quantities: [],
      revenue: "10.00",
      receipts_count: 1,
    });
    expect(legacy?.quantity).toMatch(/^1(?:\.0+)?$/);
  }
}

async function verifyTopProducts(
  page: Page,
  api: APIRequestContext,
  fixture: ReportFixture,
  from: string,
  to: string,
): Promise<void> {
  const params = { from, to, branch_id: fixture.branch.id, limit: 20 };
  // Revenue includes every unit kind; there is deliberately no quantity_basis filter.
  const revenueResponse = await api.get("reports/top-products", {
    params: { ...params, sort_by: "revenue" },
  });
  expect(revenueResponse.status()).toBe(200);
  assertTopProducts((await revenueResponse.json()) as TopProductsOverview, fixture, false);
  const unitsResponse = await api.get("reports/top-products", {
    params: { ...params, sort_by: "quantity", quantity_basis: "tablet" },
  });
  expect(unitsResponse.status()).toBe(200);
  assertTopProducts((await unitsResponse.json()) as TopProductsOverview, fixture, true);

  await page.getByRole("tab", { name: /^Товары/ }).click();
  const products = page.getByRole("region", { name: "Товары-лидеры" });
  await products.getByLabel("С", { exact: true }).fill(from);
  await products.getByLabel("По", { exact: true }).fill(to);
  await products.getByLabel("Аптечная точка", { exact: true }).selectOption(fixture.branch.id);
  await products.getByLabel("Рейтинг по", { exact: true }).selectOption("revenue");
  await expect(products.getByLabel("Единица рейтинга", { exact: true })).toHaveCount(0);

  const waitForRanking = (quantityOnly: boolean) =>
    page.waitForResponse((response) => {
      const url = new URL(response.url());
      return (
        response.request().method() === "GET" &&
        url.pathname === "/api/v1/reports/top-products" &&
        url.searchParams.get("from") === from &&
        url.searchParams.get("to") === to &&
        url.searchParams.get("branch_id") === fixture.branch.id &&
        url.searchParams.get("sort_by") === (quantityOnly ? "quantity" : "revenue") &&
        (!quantityOnly || url.searchParams.get("quantity_basis") === "tablet")
      );
    });
  const revenuePromise = waitForRanking(false);
  await products.getByRole("button", { name: "Показать", exact: true }).click();
  const browserRevenue = await revenuePromise;
  expect(browserRevenue.status()).toBe(200);
  assertTopProducts((await browserRevenue.json()) as TopProductsOverview, fixture, false);
  const table = products.getByRole("table", { name: "Товары-лидеры за выбранный период" });
  const physicalRow = table.getByRole("row").filter({ hasText: fixture.physical.brand_name });
  await expect(physicalRow).toContainText("19 табл.");
  await expect(physicalRow).toContainText("8,34 TJS");
  await expect(physicalRow).not.toContainText("уп.");
  if (fixture.legacy) {
    const legacyRow = table.getByRole("row").filter({ hasText: fixture.legacy.brand_name });
    await expect(legacyRow).toContainText("1 уп.");
    await expect(legacyRow).toContainText("10,00 TJS");
    await expect(legacyRow).not.toContainText("табл.");
  }

  await products.getByLabel("Рейтинг по", { exact: true }).selectOption("quantity");
  await products.getByLabel("Единица рейтинга", { exact: true }).selectOption("tablet");
  await expect(products.getByText(/Условия изменены/)).toBeVisible();
  const unitsPromise = waitForRanking(true);
  await products.getByRole("button", { name: "Показать", exact: true }).click();
  const browserUnits = await unitsPromise;
  expect(browserUnits.status()).toBe(200);
  assertTopProducts((await browserUnits.json()) as TopProductsOverview, fixture, true);
  await expect(table.locator("tbody tr")).toHaveCount(1);
  await expect(physicalRow).toContainText("19 табл.");
  await expect(physicalRow).toContainText("8,34 TJS");
  await expect(physicalRow).not.toContainText("уп.");
}

function assertStockOnDate(
  report: StockOnDateOverview,
  fixture: ReportFixture,
  canViewCosts: boolean,
  physicalOnly: boolean,
): void {
  const hasLegacy = Boolean(fixture.legacy) && !physicalOnly;
  expect(report.rows).toHaveLength(hasLegacy ? 2 : 1);
  expect(report.total).toBe(hasLegacy ? 2 : 1);
  expect(report.total_qty).toMatch(hasLegacy ? /^3(?:\.0+)?$/ : /^0(?:\.0+)?$/);
  expect(report.physical_quantities).toEqual([{ unit: "tablet", quantity: "60" }]);
  expect(report.cost_status).toBe(canViewCosts ? "known" : "hidden");
  expect(report.total_value).toBe(canViewCosts ? (hasLegacy ? "30.00" : "15.00") : null);
  expect(report.known_total_value).toBe(canViewCosts ? (hasLegacy ? "30.00" : "15.00") : null);
  const physical = report.rows.find((row) => row.inventory_kind === "physical");
  expect(physical).toMatchObject({
    name: fixture.physical.brand_name,
    physical_stock: {
      packaging: { version: 2, unit: "tablet", units_per_pack: 20 },
      closed_packs: "3",
      opened_units: "0",
      base_units: "60",
    },
    cost_status: canViewCosts ? "known" : "hidden",
    value: canViewCosts ? "15.00" : null,
    purchase_price: canViewCosts ? "5.00" : null,
  });
  expect(physical?.stock_id).toMatch(/^[0-9a-f-]{36}$/i);
  expect(physical?.qty).toMatch(/^0(?:\.0+)?$/);
  if (hasLegacy) {
    const legacy = report.rows.find((row) => row.inventory_kind === "legacy");
    expect(legacy).toMatchObject({
      name: fixture.legacy?.brand_name,
      physical_stock: null,
      cost_status: canViewCosts ? "known" : "hidden",
      value: canViewCosts ? "15.00" : null,
    });
    expect(legacy?.qty).toMatch(/^3(?:\.0+)?$/);
  }
}

// Read the real ZIP32 workbook using Node's built-in decompressor; no XLSX dependency.
function workbookXml(bytes: Buffer): { sheet: string; strings?: string } {
  const files = new Map<string, string>();
  let end = bytes.length - 22;
  while (end >= Math.max(0, bytes.length - 65_557) && bytes.readUInt32LE(end) !== 0x06054b50) end--;
  if (end < 0 || bytes.readUInt32LE(end) !== 0x06054b50) throw new Error("XLSX ZIP footer missing");
  let offset = bytes.readUInt32LE(end + 16);
  const count = bytes.readUInt16LE(end + 10);
  for (let index = 0; index < count; index++) {
    expect(bytes.readUInt32LE(offset)).toBe(0x02014b50);
    const method = bytes.readUInt16LE(offset + 10);
    const size = bytes.readUInt32LE(offset + 20);
    const nameLength = bytes.readUInt16LE(offset + 28);
    const extraLength = bytes.readUInt16LE(offset + 30);
    const commentLength = bytes.readUInt16LE(offset + 32);
    const local = bytes.readUInt32LE(offset + 42);
    const name = bytes.subarray(offset + 46, offset + 46 + nameLength).toString();
    if (name === "xl/worksheets/sheet1.xml" || name === "xl/sharedStrings.xml") {
      expect(bytes.readUInt32LE(local)).toBe(0x04034b50);
      const start = local + 30 + bytes.readUInt16LE(local + 26) + bytes.readUInt16LE(local + 28);
      const compressed = bytes.subarray(start, start + size);
      if (method !== 0 && method !== 8) throw new Error("Unsupported XLSX ZIP compression");
      files.set(name, (method === 8 ? inflateRawSync(compressed) : compressed).toString());
    }
    offset += 46 + nameLength + extraLength + commentLength;
  }
  const sheet = files.get("xl/worksheets/sheet1.xml");
  if (!sheet) throw new Error("XLSX worksheet missing");
  return { sheet, strings: files.get("xl/sharedStrings.xml") };
}

async function assertStockWorkbook(
  page: Page,
  bytes: Buffer,
  fixture: ReportFixture,
  canViewCosts: boolean,
  physicalOnly: boolean,
): Promise<void> {
  const rows = await page.evaluate(({ sheet, strings }) => {
    const parser = new DOMParser();
    const shared = strings
      ? Array.from(
          parser.parseFromString(strings, "application/xml").getElementsByTagName("si"),
        ).map((item) => item.textContent ?? "")
      : [];
    return Array.from(
      parser.parseFromString(sheet, "application/xml").getElementsByTagName("row"),
    ).map((row) => {
      const values: string[] = [];
      for (const cell of row.getElementsByTagName("c")) {
        const letters = cell.getAttribute("r")?.match(/^[A-Z]+/)?.[0] ?? "A";
        const column =
          Array.from(letters).reduce((sum, letter) => sum * 26 + letter.charCodeAt(0) - 64, 0) - 1;
        const value = cell.getElementsByTagName("v")[0]?.textContent ?? "";
        values[column] =
          cell.getAttribute("t") === "inlineStr"
            ? (cell.getElementsByTagName("is")[0]?.textContent ?? "")
            : cell.getAttribute("t") === "s"
              ? (shared[Number(value)] ?? "")
              : value;
      }
      return values;
    });
  }, workbookXml(bytes));
  const headerIndex = rows.findIndex((row) => row.includes("Наименование"));
  const headers = rows[headerIndex];
  if (!headers) throw new Error("Stock workbook headers missing");
  const totalIndex = rows.findIndex((row) => row[0] === "ИТОГО");
  expect(totalIndex).toBeGreaterThan(headerIndex);
  const dataRows = rows.slice(headerIndex + 1, totalIndex);
  expect(dataRows).toHaveLength(fixture.legacy && !physicalOnly ? 2 : 1);
  const physical = dataRows.find((row) => row[0] === fixture.physical.brand_name);
  if (!physical) throw new Error("Physical workbook row missing");
  for (const [column, value] of [
    ["Учёт", "Физический"],
    ["Единица", "табл."],
    ["Закрытых пачек", "3"],
    ["Единиц во вскрытых пачках", "0"],
    ["Базовых единиц", "60"],
    ["Статус стоимости", canViewCosts ? "Известна" : "Скрыто"],
    ["Стоимость", canViewCosts ? "15" : "Скрыто"],
    ["Кол-во", "—"],
  ] as const)
    expect(physical[headers.indexOf(column)]).toBe(value);
  if (fixture.legacy && !physicalOnly) {
    const legacy = dataRows.find((row) => row[0] === fixture.legacy?.brand_name);
    expect(legacy?.[headers.indexOf("Учёт")]).toBe("Обычные упаковки");
    expect(legacy?.[headers.indexOf("Кол-во")]).toBe("3");
    expect(legacy?.[headers.indexOf("Стоимость")]).toBe(canViewCosts ? "15" : "Скрыто");
  }
  const hasLegacy = Boolean(fixture.legacy) && !physicalOnly;
  expect(rows[totalIndex]?.[headers.indexOf("Кол-во")]).toBe(hasLegacy ? "3" : "0");
  expect(rows[totalIndex]?.[headers.indexOf("Стоимость")]).toBe(
    canViewCosts ? (hasLegacy ? "30" : "15") : "Скрыто",
  );
  const unitsTotal = rows.find((row) => row[0] === "ФИЗИЧЕСКИЕ ОСТАТКИ: табл.");
  expect(unitsTotal?.[headers.indexOf("Кол-во")]).toBe("60");
}

async function verifyStockOnDate(
  page: Page,
  api: APIRequestContext,
  fixture: ReportFixture,
  date: string,
): Promise<void> {
  const meResponse = await api.get("auth/me");
  expect(meResponse.ok()).toBe(true);
  const me = (await meResponse.json()) as MeResponse;
  const costScope = me.permission_scopes?.["batches.view_costs"];
  const canViewCosts =
    me.permissions.includes("batches.view_costs") &&
    (costScope === null || costScope?.includes(fixture.branch.id) === true);
  const params = { date, branch_id: fixture.branch.id, page: 1, page_size: 25 };
  for (const physicalOnly of [false, true]) {
    const response = await api.get("reports/stock-on-date", {
      params: { ...params, ...(physicalOnly ? { query: fixture.physical.brand_name } : {}) },
    });
    expect(response.status()).toBe(200);
    assertStockOnDate(
      (await response.json()) as StockOnDateOverview,
      fixture,
      canViewCosts,
      physicalOnly,
    );
  }
  await page.getByRole("tab", { name: /^Остатки/ }).click();
  const stock = page.getByRole("region", { name: "Остатки и сроки годности" });
  await stock.getByLabel("Дата остатка", { exact: true }).fill(date);
  await stock.getByLabel("Аптечная точка", { exact: true }).selectOption(fixture.branch.id);
  const table = stock.getByRole("table", { name: "Остатки лекарств на выбранную дату" });
  for (const physicalOnly of [false, true]) {
    const search = physicalOnly ? fixture.physical.brand_name : "";
    await stock.getByLabel("Товар, МНН или партия", { exact: true }).fill(search);
    const reportPromise = page.waitForResponse((response) => {
      const url = new URL(response.url());
      return (
        url.pathname === "/api/v1/reports/stock-on-date" &&
        url.searchParams.get("date") === date &&
        url.searchParams.get("branch_id") === fixture.branch.id &&
        (url.searchParams.get("query") ?? "") === search
      );
    });
    await stock.getByRole("button", { name: "Показать", exact: true }).click();
    const response = await reportPromise;
    expect(response.status()).toBe(200);
    assertStockOnDate(
      (await response.json()) as StockOnDateOverview,
      fixture,
      canViewCosts,
      physicalOnly,
    );
    await expect(table.locator("tbody tr")).toHaveCount(fixture.legacy && !physicalOnly ? 2 : 1);
    const physical = table.getByRole("row").filter({ hasText: fixture.physical.brand_name });
    await expect(physical).toContainText("60 табл.");
    await expect(physical).toContainText("Закрытых упаковок: 3");
    await expect(physical).toContainText("В открытых упаковках: 0 табл.");
    await expect(physical).not.toContainText("0 уп.");
    await expect(physical).toContainText(canViewCosts ? "15,00 TJS" : "Скрыто");
    const hasLegacy = Boolean(fixture.legacy) && !physicalOnly;
    const totals = stock.getByRole("region", { name: "Итоги остатков" });
    await expect(totals.getByText("Обычные упаковки", { exact: true }).locator("..")).toContainText(
      hasLegacy ? "3 уп." : "0 уп.",
    );
    await expect(
      totals.getByText("Физические единицы", { exact: true }).locator(".."),
    ).toContainText("60 табл.");
    await expect(
      totals.getByText("Закупочная стоимость", { exact: true }).locator(".."),
    ).toContainText(canViewCosts ? (hasLegacy ? "30,00 TJS" : "15,00 TJS") : "Скрыто");
    if (fixture.legacy && !physicalOnly) {
      const legacy = table.getByRole("row").filter({ hasText: fixture.legacy.brand_name });
      await expect(legacy).toContainText("3 уп.");
      await expect(legacy).not.toContainText("табл.");
    }
    const downloadUrl = await downloadXlsx(
      page,
      "/api/v1/reports/stock-on-date.xlsx",
      `stock-${date}.xlsx`,
      () => stock.getByRole("button", { name: "Скачать в Excel", exact: true }).click(),
      async (bytes) => assertStockWorkbook(page, bytes, fixture, canViewCosts, physicalOnly),
    );
    expect(downloadUrl.searchParams.get("date")).toBe(date);
    expect(downloadUrl.searchParams.get("branch_id")).toBe(fixture.branch.id);
    expect(downloadUrl.searchParams.get("query") ?? "").toBe(search);
  }
}

async function refundOneUnit(api: APIRequestContext, scenario: Scenario, sale: CompletedSale) {
  const prefix = `sales/${sale.sale_id}/${scenario.kind}-cash-refunds`;
  const optionsResponse = await api.get(`${prefix}/options`);
  expect(optionsResponse.ok()).toBe(true);
  const options = (await optionsResponse.json()) as {
    lines: { inventory_kind?: string; line_id: string }[];
  };
  const line = options.lines.find(
    (item) => scenario.kind === "physical" || item.inventory_kind === "physical_unit",
  );
  expect(line).toBeDefined();
  if (!line) throw new Error("Original physical sale line is missing");
  const selection = { line_id: line.line_id, sale_format: "unit", quantity: 1 };
  const items = [
    scenario.kind === "mixed" ? { ...selection, inventory_kind: "physical_unit" } : selection,
  ];
  const quoted = await api.post(`${prefix}/quote`, { data: { items } });
  expect(quoted.ok()).toBe(true);
  const quote = (await quoted.json()) as { total_amount: string; revision_hash: string };
  // The original pack's net is allocated to units in whole cents.
  expect(quote.total_amount).toBe("0.43");
  const returned = await api.post(`${prefix}/complete`, {
    data: {
      operation_id: crypto.randomUUID(),
      items,
      shift_id: sale.shift_id,
      expected_total_amount: "0.43",
      expected_revision_hash: quote.revision_hash,
      reason_code: "quality_issue",
      comment: "E2E report partial unit return",
    },
  });
  expect(returned.ok()).toBe(true);
  const result = (await returned.json()) as {
    receipt: { completed_at: string };
    total_amount: string;
  };
  expect(result.total_amount).toBe("0.43");
  return result;
}

function calendarDay(timestamp: string, timezone: string): string {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: timezone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date(timestamp));
  return ["year", "month", "day"]
    .map((type) => parts.find((part) => part.type === type)?.value)
    .join("-");
}

function assertSummary(
  summary: SalesSummaryOverview,
  scenario: Scenario,
  saleDay: string,
  refundDay?: string,
): void {
  expect(summary).toMatchObject({
    gross_sales: scenario.gross,
    total_discounts: "1.23",
    total_refunds: refundDay ? "0.43" : "0.00",
    net: refundDay ? scenario.net : scenario.paid,
    sales_count: 1,
    returns_count: refundDay ? 1 : 0,
  });
  expect(summary.daily).toHaveLength(refundDay && refundDay !== saleDay ? 2 : 1);
  for (const daily of summary.daily) {
    const hasSale = daily.day === saleDay;
    const hasRefund = daily.day === refundDay;
    expect(daily).toMatchObject({
      gross_sales: hasSale ? scenario.gross : "0.00",
      total_discounts: hasSale ? "1.23" : "0.00",
      total_refunds: hasRefund ? "0.43" : "0.00",
      net: hasSale ? (hasRefund ? scenario.net : scenario.paid) : "-0.43",
      sales_count: hasSale ? 1 : 0,
      returns_count: hasRefund ? 1 : 0,
    });
  }
}

async function downloadXlsx(
  page: Page,
  path: string,
  filename: string,
  click: () => Promise<void>,
  inspect?: (bytes: Buffer) => Promise<void>,
) {
  const responsePromise = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === path && response.request().method() === "GET",
  );
  const downloadPromise = page.waitForEvent("download");
  await click();
  const response: Response = await responsePromise;
  expect(response.status()).toBe(200);
  expect(response.headers()["content-type"]).toContain(XLSX_MIME);
  const bytes = await response.body();
  expect(bytes.length).toBeGreaterThan(1000);
  expect(bytes.subarray(0, 2).toString()).toBe("PK");
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe(filename);
  expect(await download.failure()).toBeNull();
  const file = await download.path();
  expect(file).not.toBeNull();
  if (!file) throw new Error("XLSX download file is missing");
  expect(await readFile(file)).toEqual(bytes);
  await inspect?.(bytes);
  return new URL(response.url());
}

for (const scenario of cases) {
  test(`${scenario.kind} v${scenario.version}: reports preserve pack discounts after a one-unit refund`, async ({
    page,
  }) => {
    test.setTimeout(180_000);
    const anonymous = await request.newContext();
    let api: APIRequestContext | undefined;
    try {
      const tokens = await apiLogin(anonymous, OWNER);
      api = await apiContext(tokens.access_token);
      const fixture = await seedSale(api, scenario);
      const { branch, register, sale } = fixture;
      const settingsResponse = await api.get("tenant/operational-settings");
      expect(settingsResponse.ok()).toBe(true);
      const settings = (await settingsResponse.json()) as { report_timezone: string };
      const saleDay = calendarDay(sale.completed_at, settings.report_timezone);
      const before = await api.get("reports/sales-summary", {
        params: { from: saleDay, to: saleDay, branch_id: branch.id },
      });
      expect(before.ok()).toBe(true);
      assertSummary((await before.json()) as SalesSummaryOverview, scenario, saleDay);
      const refund = await refundOneUnit(api, scenario, sale);
      const refundDay = calendarDay(refund.receipt.completed_at, settings.report_timezone);
      const [from, to] = [saleDay, refundDay].sort();
      expect(from).toBeDefined();
      expect(to).toBeDefined();
      if (!from || !to) throw new Error("Report calendar range is missing");
      const closed = await api.post(`shifts/${sale.shift_id}/close`, {
        data: { closing_cash_actual: scenario.cash },
      });
      expect(closed.ok()).toBe(true);
      const zResponse = await api.get(`shifts/${sale.shift_id}/z-report`);
      expect(zResponse.ok()).toBe(true);
      expect(await zResponse.json()).toMatchObject({
        closing_cash_expected: scenario.cash,
        closing_difference: "0.00",
        sales_count: 1,
        returns_count: 1,
        totals: { sales_total: scenario.gross, discounts_total: "1.23", returns_total: "0.43" },
      });

      await loginInBrowser(page, OWNER);
      await page.goto("/reports");
      await page.getByRole("tab", { name: /^Продажи/ }).click();
      const overview = page.getByRole("region", { name: "Продажи за период" });
      await overview.getByLabel("С", { exact: true }).fill(from);
      await overview.getByLabel("По", { exact: true }).fill(to);
      await overview.getByLabel("Аптечная точка", { exact: true }).selectOption(branch.id);
      const summaryPromise = page.waitForResponse((response) => {
        const url = new URL(response.url());
        return (
          url.pathname === "/api/v1/reports/sales-summary" &&
          url.searchParams.get("branch_id") === branch.id &&
          url.searchParams.get("from") === from &&
          url.searchParams.get("to") === to
        );
      });
      await overview.getByRole("button", { name: "Обновить сводку" }).click();
      const summaryResponse = await summaryPromise;
      expect(summaryResponse.status()).toBe(200);
      const summary = (await summaryResponse.json()) as SalesSummaryOverview;
      assertSummary(summary, scenario, saleDay, refundDay);
      for (const [label, amount] of [
        ["Скидки", "1.23"],
        ["Продажи", scenario.gross],
        ["Чистая выручка", scenario.net],
      ] as const) {
        await expect(overview.getByText(label, { exact: true }).locator("..")).toContainText(
          `${amount.replace(".", ",")} TJS`,
        );
      }
      const trend = overview
        .locator("section")
        .filter({ has: page.getByRole("heading", { name: "Динамика по дням" }) });
      for (const day of summary.daily) {
        await expect(trend).toContainText(`${day.net.replace(".", ",")} TJS`);
      }
      const summaryUrl = await downloadXlsx(
        page,
        "/api/v1/reports/sales-summary.xlsx",
        `sales-summary-${from}_${to}.xlsx`,
        () => overview.getByRole("button", { name: "Скачать XLSX" }).click(),
      );
      expect(summaryUrl.searchParams.get("branch_id")).toBe(branch.id);
      expect(summaryUrl.searchParams.get("from")).toBe(from);
      expect(summaryUrl.searchParams.get("to")).toBe(to);

      await verifyTopProducts(page, api, fixture, from, to);
      await verifyStockOnDate(
        page,
        api,
        fixture,
        calendarDay(new Date().toISOString(), settings.report_timezone),
      );

      await page.getByRole("tab", { name: /^Смены/ }).click();
      const history = page.getByRole("region", { name: "Закрытые смены и итоги" });
      await history.getByLabel("Открыта с", { exact: true }).fill(from);
      await history.getByLabel("По", { exact: true }).fill(to);
      await history.getByLabel("Аптечная точка", { exact: true }).selectOption(branch.id);
      await history.getByRole("button", { name: "Показать", exact: true }).click();
      const row = history.getByRole("row").filter({ hasText: register.name });
      await expect(row).toContainText(branch.name);
      await row.getByRole("button", { name: "Открыть", exact: true }).click();
      const dialog = page.getByRole("dialog", { name: `Итог смены (Z-отчёт) · ${register.name}` });
      await downloadXlsx(
        page,
        `/api/v1/shifts/${sale.shift_id}/z-report.xlsx`,
        `z-report-${sale.shift_id}.xlsx`,
        () => dialog.getByRole("button", { name: "Скачать XLSX" }).click(),
      );
    } finally {
      await api?.dispose();
      await anonymous.dispose();
    }
  });
}
