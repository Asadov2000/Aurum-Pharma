import { request, test, type APIRequestContext, type Locator, type Page } from "@playwright/test";

import { dockerExec, E2E_POSTGRES_CONTAINER, E2E_POSTGRES_DB } from "./docker";
import {
  apiContext,
  apiLogin,
  clearLoginRateLimit,
  expect,
  loginInBrowser,
  OWNER,
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
interface Decision {
  decision_operation_id: string;
  branch_id: string;
  parent_sale_id: string;
  intake_kind: "cash_refund" | "zero_return";
  state: "resolved";
  target: { inventory_kind: "physical_unit" };
}
interface Fixture {
  sale: Sale;
  physicalName: string;
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

async function seedQuarantine(api: APIRequestContext, zero: boolean): Promise<Fixture> {
  const branch = await seedBranch(api, uniqueName("Decision branch"));
  const register = await seedRegister(api, branch.id, uniqueName("Decision register"));
  const physical = await seedCatalogItem(api, uniqueName("Decision tablets"), "10.00");
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
  expect(
    (
      await api.put(`sales/${draft.id}/physical-cart/items/${crypto.randomUUID()}`, {
        data: {
          catalog_id: physical.id,
          position: 1,
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
  expect(
    (await api.post(`sales/${draft.id}/physical-cash/intent`, { data: confirmation })).ok(),
  ).toBe(true);
  const paid = await api.post(`sales/${draft.id}/physical-cash/complete`, { data: confirmation });
  expect(paid.ok()).toBe(true);
  const sale = (await paid.json()) as Sale;
  const prefix = `sales/${sale.sale_id}/physical-cash-refunds`;
  const optionsResponse = await api.get(`${prefix}/options`);
  expect(optionsResponse.ok()).toBe(true);
  const options = (await optionsResponse.json()) as {
    lines: { inventory_kind: string; line_id: string }[];
  };
  const items = options.lines.map((line) => ({
    line_id: line.line_id,
    sale_format: "unit",
    quantity: 1,
  }));
  const refundQuoteResponse = await api.post(`${prefix}/quote`, { data: { items } });
  expect(refundQuoteResponse.ok()).toBe(true);
  const refundQuote = (await refundQuoteResponse.json()) as {
    total_amount: string;
    revision_hash: string;
  };
  expect(refundQuote.total_amount).toBe(zero ? "0.00" : "0.50");
  const payload = {
    operation_id: crypto.randomUUID(),
    items,
    expected_revision_hash: refundQuote.revision_hash,
    reason_code: "quality_issue",
    comment: "E2E quarantine intake",
  };
  const intake = await api.post(
    zero ? `sales/${sale.sale_id}/physical-zero-returns/complete` : `${prefix}/complete`,
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
        data: { closing_cash_actual: zero ? "100.01" : "109.50" },
      })
    ).ok(),
  ).toBe(true);
  return {
    sale,
    physicalName: physical.brand_name,
    physicalId: physical.id,
  };
}

function persistedFacts(fixture: Fixture): { decisions: number; unchanged: unknown } {
  const { sale, physicalId } = fixture;
  for (const id of [sale.sale_id, physicalId])
    expect(id).toMatch(/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i);
  // SELECT only; dockerExec refuses containers outside the disposable E2E project.
  // Hashes compare immutable documents without printing receipt or procurement data.
  const sql = `SELECT json_build_object(
    'decisions',(SELECT count(*) FROM physical_customer_return_disposition WHERE parent_sale_id='${sale.sale_id}'),
    'unchanged',json_build_object(
      'sales',(SELECT md5(string_agg(to_jsonb(s)::text,'' ORDER BY s.id)) FROM sale s WHERE s.id='${sale.sale_id}' OR s.parent_sale_id='${sale.sale_id}'),
      'payments',(SELECT md5(string_agg(to_jsonb(p)::text,'' ORDER BY p.id)) FROM sale_payment p JOIN sale s ON s.id=p.sale_id WHERE s.id='${sale.sale_id}' OR s.parent_sale_id='${sale.sale_id}'),
      'physical_movements',(SELECT count(*) FROM unit_stock_movement_v2 m JOIN unit_stock_lot_v2 l ON l.id=m.lot_id WHERE l.catalog_id='${physicalId}'),
      'physical_lots',(SELECT md5(string_agg(to_jsonb(l)::text,'' ORDER BY l.id)) FROM unit_stock_lot_v2 l WHERE l.catalog_id='${physicalId}'),
      'physical_opened',(SELECT md5(string_agg(to_jsonb(p)::text,'' ORDER BY p.id)) FROM unit_stock_opened_pack_v2 p JOIN unit_stock_lot_v2 l ON l.id=p.lot_id WHERE l.catalog_id='${physicalId}'),
      'physical_remaining',(SELECT sum(l.closed_packs*20+COALESCE((SELECT sum(p.remaining_units) FROM unit_stock_opened_pack_v2 p WHERE p.lot_id=l.id),0)) FROM unit_stock_lot_v2 l WHERE l.catalog_id='${physicalId}')
    ));`;
  return JSON.parse(
    dockerExec(E2E_POSTGRES_CONTAINER, [
      "psql",
      "-U",
      "postgres",
      "-d",
      E2E_POSTGRES_DB,
      "-At",
      "-c",
      sql,
    ]).trim(),
  ) as { decisions: number; unchanged: unknown };
}

async function openQueue(page: Page, sale: Sale): Promise<Locator> {
  await page.goto("/batches");
  await page.getByRole("button", { name: "Возвраты покупателей", exact: true }).click();
  await page.getByRole("button", { name: "Физические возвраты", exact: true }).click();
  const panel = page.getByRole("region", { name: "Физические возвраты в карантине", exact: true });
  await panel.getByLabel("Точка", { exact: true }).selectOption(sale.branch_id);
  await panel.getByLabel("Поиск", { exact: true }).fill(sale.receipt_number);
  return panel;
}

async function fillDecision(dialog: Locator, action: string): Promise<void> {
  await dialog.getByRole("radio", { name: action, exact: true }).check();
  await dialog.getByLabel("Причина решения", { exact: true }).selectOption("quality_issue");
  await dialog.getByLabel("Комментарий к решению", { exact: true }).fill("E2E completed action");
  await dialog
    .getByLabel("Подтверждаю, что действие фактически выполнено для всего указанного количества", {
      exact: true,
    })
    .check();
}

for (const zero of [false, true]) {
  test(`recovers physical ${zero ? "zero" : "cash"} quarantine decisions without stock or payment writes`, async ({
    page,
  }) => {
    test.setTimeout(180_000);
    clearLoginRateLimit(OWNER.email);
    const anonymous = await request.newContext();
    const tokens = await apiLogin(anonymous, OWNER);
    const api = await apiContext(tokens.access_token);
    try {
      const fixture = await seedQuarantine(api, zero);
      const before = persistedFacts(fixture);
      expect(before.decisions).toBe(0);
      await page.setViewportSize({ width: 1366, height: 768 });
      await loginInBrowser(page, OWNER);
      const submitted: unknown[] = [];
      let resolved: Decision | null = null;
      let blockStatus = false;
      let statusRequests = 0;
      await page.route(
        "**/api/v1/customer-returns/physical/dispositions/operations/**",
        async (route) => {
          statusRequests += 1;
          if (blockStatus) await route.abort("failed");
          else await route.continue();
        },
      );
      await page.route("**/api/v1/customer-returns/physical/dispositions", async (route) => {
        if (route.request().method() !== "POST") return route.continue();
        submitted.push(route.request().postDataJSON() as unknown);
        const durableRequests = await page.evaluate(() =>
          Object.keys(localStorage)
            .filter((key) => key.startsWith("customerReturns:physicalDisposition:v1:"))
            .map(
              (key) =>
                (JSON.parse(localStorage.getItem(key) ?? "null") as { request: unknown }).request,
            ),
        );
        expect(durableRequests).toEqual([submitted[submitted.length - 1]]);
        if (submitted.length > 1) return route.continue();
        const response = await route.fetch();
        expect(response.ok()).toBe(true);
        resolved = (await response.json()) as Decision;
        blockStatus = true;
        await route.abort("failed");
      });
      let panel = await openQueue(page, fixture.sale);
      const table = panel.getByRole("table", { name: "Очередь физических возвратов", exact: true });
      await expect(table.getByRole("row")).toHaveCount(2);
      await table
        .getByRole("row")
        .filter({ hasText: fixture.physicalName })
        .getByRole("button", { name: "Оформить решение", exact: true })
        .click();
      const dialog = page.getByRole("dialog", {
        name: "Решение по физическому карантину",
        exact: true,
      });
      await fillDecision(dialog, "Утилизировано");
      await dialog.getByRole("button", { name: "Сохранить решение", exact: true }).click();
      await expect.poll(() => resolved).not.toBeNull();
      const original = resolved as unknown as Decision;
      expect(original).toMatchObject({
        state: "resolved",
        branch_id: fixture.sale.branch_id,
        parent_sale_id: fixture.sale.sale_id,
        intake_kind: zero ? "zero_return" : "cash_refund",
        target: { inventory_kind: "physical_unit" },
      });
      expect(submitted).toHaveLength(1);
      expect(submitted[0]).toMatchObject({ decision_operation_id: original.decision_operation_id });
      await expect(
        dialog.getByRole("button", { name: "Проверить результат", exact: true }),
      ).toBeEnabled();
      await page.reload();
      panel = await openQueue(page, fixture.sale);
      await panel
        .getByRole("button", { name: "Проверить сохранённое решение", exact: true })
        .click();
      await expect(
        dialog.getByRole("button", { name: "Проверить результат", exact: true }),
      ).toBeEnabled();
      expect(submitted).toHaveLength(1);
      expect(statusRequests).toBeGreaterThan(0);
      const statusPath = `/api/v1/customer-returns/physical/dispositions/operations/${original.decision_operation_id}`;
      blockStatus = false;
      const recovered = page.waitForResponse((response) => {
        const url = new URL(response.url());
        return (
          url.pathname === statusPath &&
          url.searchParams.get("branch_id") === fixture.sale.branch_id
        );
      });
      await dialog.getByRole("button", { name: "Проверить результат", exact: true }).click();
      const recovery = await recovered;
      expect(recovery.ok()).toBe(true);
      expect(await recovery.json()).toEqual(original);
      await expect(dialog.getByText("Решение сохранено", { exact: true })).toBeVisible();
      await dialog.getByRole("button", { name: "Завершить проверку", exact: true }).click();
      await expect(dialog).not.toBeVisible();
      expect(submitted).toHaveLength(1);
      expect(persistedFacts(fixture)).toEqual({ ...before, decisions: 1 });
      const remaining = await api.get("customer-returns/physical", {
        params: { branch_id: fixture.sale.branch_id },
      });
      expect(remaining.ok()).toBe(true);
      expect(await remaining.json()).toMatchObject({ items: [], total: 0 });
      const history = await api.get("customer-returns/physical/dispositions/history", {
        params: { branch_id: fixture.sale.branch_id },
      });
      expect(history.ok()).toBe(true);
      const saved = (await history.json()) as { total: number; items: Decision[] };
      expect(saved.total).toBe(1);
      expect(
        saved.items.find((item) => item.decision_operation_id === original.decision_operation_id),
      ).toEqual(original);
      expect(saved.items.map((item) => item.target.inventory_kind).sort()).toEqual([
        "physical_unit",
      ]);
      await page.getByRole("button", { name: "История решений", exact: true }).click();
      const historyPanel = page.getByRole("region", {
        name: "История решений по физическому карантину",
        exact: true,
      });
      await historyPanel
        .getByLabel("Точка истории", { exact: true })
        .selectOption(fixture.sale.branch_id);
      await expect(historyPanel.getByRole("article")).toHaveCount(1);
      for (const article of await historyPanel.getByRole("article").all())
        await article.getByText("Связанные операции", { exact: true }).click();
      await expect(
        historyPanel.getByText(original.decision_operation_id, { exact: true }).first(),
      ).toBeVisible();
      expect(submitted).toHaveLength(1);
    } finally {
      await api.dispose();
      await anonymous.dispose();
    }
  });
}
