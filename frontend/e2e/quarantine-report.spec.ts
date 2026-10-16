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
} from "./helpers";

interface Sale {
  sale_id: string;
  branch_id: string;
  shift_id: string;
  receipt_number: string;
}

interface QuarantineReport {
  total: number;
  known_total_value: string | null;
  total_value: string | null;
  cost_status: "known" | "partial" | "hidden";
  rows: {
    name: string;
    branch_id: string;
    inventory_kind: "legacy" | "physical";
    source_kind: "legacy" | "physical" | "mixed";
    cost_status: "known" | "unknown" | "hidden";
    value: string | null;
  }[];
}

for (const scenario of [
  { name: "physical cash refund", mixed: false, zero: false },
  { name: "mixed cash refund", mixed: true, zero: false },
  { name: "mixed zero return", mixed: true, zero: true },
]) {
  test(`quarantine valuation for ${scenario.name} preserves known costs and exports the selected branch`, async ({
    page,
  }) => {
    test.setTimeout(180_000);
    clearLoginRateLimit(OWNER.email);
    const anonymous = await request.newContext();
    const tokens = await apiLogin(anonymous, OWNER);
    const api = await apiContext(tokens.access_token);
    try {
      const fixture = await seedQuarantine(api, scenario.zero, scenario.mixed);
      const emptyBranch = await seedBranch(api, uniqueName("Empty quarantine report"));
      await page.setViewportSize({ width: 1366, height: 900 });
      await loginInBrowser(page, OWNER);
      await page.goto("/reports");
      // Report tabs include their description in the accessible name on desktop.
      await page.getByRole("tab", { name: /^Карантин(?:\s|$)/ }).click();
      const panel = page.getByRole("region", { name: "Карантин на дату", exact: true });
      // Use the page's tenant-local date instead of the test runner's time zone.
      const reportDate = await panel.getByLabel("Дата карантина", { exact: true }).inputValue();
      await panel
        .getByLabel("Аптечная точка", { exact: true })
        .selectOption(fixture.sale.branch_id);
      const reportResponse = page.waitForResponse((response) => {
        const url = new URL(response.url());
        return (
          url.pathname.endsWith("/reports/quarantine-on-date") &&
          url.searchParams.get("branch_id") === fixture.sale.branch_id &&
          response.request().method() === "GET"
        );
      });
      await panel.getByRole("button", { name: "Показать", exact: true }).click();
      const response = await reportResponse;
      expect(response.ok()).toBe(true);
      const report = (await response.json()) as QuarantineReport;
      expect(report.total).toBe(scenario.mixed ? 2 : 1);
      expect(report.known_total_value).toBe("0.25");
      expect(report.total_value).toBe(scenario.mixed ? null : "0.25");
      expect(report.cost_status).toBe(scenario.mixed ? "partial" : "known");
      expect(report.rows.every((row) => row.branch_id === fixture.sale.branch_id)).toBe(true);
      expect(report.rows.find((row) => row.inventory_kind === "physical")).toMatchObject({
        name: fixture.physicalName,
        source_kind: scenario.mixed ? "mixed" : "physical",
        value: "0.25",
        cost_status: "known",
      });
      const table = panel.getByRole("table", {
        name: "Неразрешённые возвраты на выбранную дату",
        exact: true,
      });
      await expect(table.getByText(fixture.physicalName, { exact: true })).toBeVisible();
      if (scenario.mixed) {
        expect(report.rows.find((row) => row.inventory_kind === "legacy")).toMatchObject({
          name: fixture.legacyName,
          source_kind: "mixed",
          value: null,
          cost_status: "unknown",
        });
        await expect(table.getByText("Нет данных о стоимости", { exact: true })).toBeVisible();
        await expect(
          panel.getByText("Стоимость части возвратов недоступна или неизвестна.", { exact: true }),
        ).toBeVisible();
      }
      const exportResponse = page.waitForResponse((downloadResponse) => {
        const url = new URL(downloadResponse.url());
        return (
          url.pathname.endsWith("/reports/quarantine-on-date.xlsx") &&
          url.searchParams.get("branch_id") === fixture.sale.branch_id &&
          url.searchParams.get("date") === reportDate
        );
      });
      const downloaded = page.waitForEvent("download");
      await panel.getByRole("button", { name: "Скачать в Excel", exact: true }).click();
      const workbook = await exportResponse;
      expect(workbook.ok()).toBe(true);
      expect(workbook.headers()["content-type"]).toContain(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      );
      expect((await workbook.body()).subarray(0, 2).toString()).toBe("PK");
      expect((await downloaded).suggestedFilename()).toMatch(/\.xlsx$/);

      await panel.getByLabel("Аптечная точка", { exact: true }).selectOption(emptyBranch.id);
      const emptyResponse = page.waitForResponse((empty) => {
        const url = new URL(empty.url());
        return (
          url.pathname.endsWith("/reports/quarantine-on-date") &&
          url.searchParams.get("branch_id") === emptyBranch.id
        );
      });
      await panel.getByRole("button", { name: "Показать", exact: true }).click();
      const empty = await emptyResponse;
      expect(empty.ok()).toBe(true);
      expect(await empty.json()).toMatchObject({ total: 0, rows: [] });
      await expect(panel.getByText(fixture.physicalName, { exact: true })).toHaveCount(0);
    } finally {
      await api.dispose();
      await anonymous.dispose();
    }
  });
}
interface Fixture {
  sale: Sale;
  legacyName: string;
  physicalName: string;
  batchId: string;
  physicalId: string;
}
async function receivePhysicalStock(
  api: APIRequestContext,
  branchId: string,
  catalogId: string,
  expiresAt: string,
): Promise<void> {
  const supplier = await seedSupplier(api, uniqueName("Decision physical supplier"));
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

async function seedQuarantine(
  api: APIRequestContext,
  zero: boolean,
  mixed: boolean,
): Promise<Fixture> {
  const branch = await seedBranch(api, uniqueName("Decision branch"));
  const register = await seedRegister(api, branch.id, uniqueName("Decision register"));
  const supplier = await seedSupplier(api, uniqueName("Decision pack supplier"));
  const legacy = await seedCatalogItem(api, uniqueName("Decision packs"), zero ? "0.01" : "10.00");
  const physical = await seedCatalogItem(api, uniqueName("Decision tablets"), "10.00");
  const expiry = new Date();
  expiry.setUTCDate(expiry.getUTCDate() + 180);
  const expiresAt = expiry.toISOString().slice(0, 10);
  const batch = await seedAcceptedBatch(api, {
    branchId: branch.id,
    supplierId: supplier.id,
    catalogId: legacy.id,
    qty: "4",
    purchasePrice: zero ? "0.01" : "5.00",
    salePrice: zero ? "0.01" : "10.00",
    expiresAt,
  });
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
  if (mixed)
    expect(
      (
        await api.post(`sales/${draft.id}/items`, {
          data: { operation_id: crypto.randomUUID(), catalog_id: legacy.id, qty: "1.000" },
        })
      ).ok(),
    ).toBe(true);
  expect(
    (
      await api.put(`sales/${draft.id}/physical-cart/items/${crypto.randomUUID()}`, {
        data: {
          catalog_id: physical.id,
          position: mixed ? 2 : 1,
          sale_format: "pack",
          quantity: 1,
          discount_amount: zero ? "9.99" : "0.00",
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
  const confirmation = {
    operation_id: crypto.randomUUID(),
    sale_id: draft.id,
    expected_revision_hash: quote.revision_hash,
    expected_total_amount: quote.quote.total_amount,
    cash_tendered: quote.quote.total_amount,
  };
  const checkout = mixed ? "mixed-cash" : "physical-cash";
  expect(
    (await api.post(`sales/${draft.id}/${checkout}/intent`, { data: confirmation })).ok(),
  ).toBe(true);
  const paid = await api.post(`sales/${draft.id}/${checkout}/complete`, { data: confirmation });
  expect(paid.ok()).toBe(true);
  const sale = (await paid.json()) as Sale;
  const prefix = `sales/${sale.sale_id}/${checkout}-refunds`;
  const optionsResponse = await api.get(`${prefix}/options`);
  expect(optionsResponse.ok()).toBe(true);
  const options = (await optionsResponse.json()) as {
    lines: { inventory_kind: string; line_id: string }[];
  };
  const items = options.lines.map((line) =>
    line.inventory_kind === "legacy_pack"
      ? { inventory_kind: "legacy_pack", line_id: line.line_id, quantity: zero ? "0.001" : "0.125" }
      : {
          ...(mixed ? { inventory_kind: "physical_unit" } : {}),
          line_id: line.line_id,
          sale_format: "unit",
          quantity: 1,
        },
  );
  const refundQuoteResponse = await api.post(`${prefix}/quote`, { data: { items } });
  expect(refundQuoteResponse.ok()).toBe(true);
  const refundQuote = (await refundQuoteResponse.json()) as {
    total_amount: string;
    revision_hash: string;
  };
  expect(refundQuote.total_amount).toBe(zero ? "0.00" : mixed ? "1.75" : "0.50");
  const payload = {
    operation_id: crypto.randomUUID(),
    items,
    expected_revision_hash: refundQuote.revision_hash,
    reason_code: "quality_issue",
    comment: "E2E quarantine intake",
  };
  const intake = await api.post(
    zero
      ? `sales/${sale.sale_id}/${mixed ? "mixed" : "physical"}-zero-returns/complete`
      : `${prefix}/complete`,
    {
      data: zero
        ? payload
        : { ...payload, shift_id: sale.shift_id, expected_total_amount: refundQuote.total_amount },
    },
  );
  expect(intake.ok()).toBe(true);
  expect(
    (
      await api.post(`shifts/${sale.shift_id}/close`, {
        data: {
          closing_cash_actual: zero ? (mixed ? "100.02" : "100.01") : mixed ? "118.25" : "109.50",
        },
      })
    ).ok(),
  ).toBe(true);
  return {
    sale,
    legacyName: legacy.brand_name,
    physicalName: physical.brand_name,
    batchId: batch.id,
    physicalId: physical.id,
  };
}
