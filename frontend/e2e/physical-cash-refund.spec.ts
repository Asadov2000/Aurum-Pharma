import { request, test, type APIRequestContext, type Page } from "@playwright/test";

import { dockerExec, E2E_POSTGRES_CONTAINER, E2E_POSTGRES_DB } from "./docker";
import {
  apiContext,
  apiLogin,
  catalogSearchKey,
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

interface CheckoutResult {
  schema_version: number;
  sale_id: string;
  operation_id: string;
  shift_id: string;
  stock_command_id: string;
  receipt_number: string;
  total_amount: string;
  lines: { sale_format: string; quantity: number; base_units: number }[];
  payments: { id: string; method: string; amount: string }[];
}

async function receivePhysicalStock(api: APIRequestContext, branchId: string, catalogId: string) {
  const supplier = await seedSupplier(api, uniqueName("Physical refund supplier"));
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
  const expiry = new Date();
  expiry.setUTCDate(expiry.getUTCDate() + 180);
  const added = await api.post(`incoming/${document.id}/items`, {
    data: {
      operation_id: crypto.randomUUID(),
      catalog_id: catalogId,
      expires_at: expiry.toISOString().slice(0, 10),
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

async function addPhysicalFormats(page: Page, name: string): Promise<string> {
  await page.getByRole("button", { name: "Выбрать упаковку, блистер или штуки" }).click();
  const editor = page.getByRole("region", { name: "Продажа упаковками, блистерами и поштучно" });
  await expect(editor).toBeVisible();
  let saleId = "";
  for (const [format, label, quantity] of [
    ["Поштучно", "Количество, шт.", "2"],
    ["Блистер", "Количество блистеров", "1"],
    ["Упаковка", "Количество упаковок", "1"],
  ] as const) {
    await editor.getByLabel("Название лекарства").fill(catalogSearchKey(name));
    await editor.getByRole("option", { name: new RegExp(name) }).click();
    await editor.getByRole("radio", { name: format, exact: true }).click();
    await editor.getByLabel(label, { exact: true }).fill(quantity);
    const saved = page.waitForResponse(
      (response) =>
        response.request().method() === "PUT" &&
        /\/sales\/[^/]+\/physical-cart\/items\/[^/]+$/.test(new URL(response.url()).pathname),
    );
    await editor.getByRole("button", { name: "Добавить в корзину", exact: true }).click();
    const response = await saved;
    expect(response.ok()).toBe(true);
    saleId = ((await response.json()) as { sale_id: string }).sale_id;
    await expect(editor.getByLabel("Название лекарства")).toHaveValue("");
  }
  await expect(editor.getByRole("listitem")).toHaveCount(3);
  return saleId;
}

interface RefundResult {
  state: "completed";
  operation_id: string;
  parent_sale_id: string;
  sale_id: string;
  total_amount: string;
  receipt: {
    receipt_number: string;
    lines: { sale_format: string; quantity: number; base_units: number }[];
  };
}

async function openRefund(page: Page, sale: CheckoutResult): Promise<void> {
  const filtered = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return (
      response.request().method() === "GET" &&
      url.pathname === "/api/v1/sales" &&
      url.searchParams.get("receipt_number") === sale.receipt_number
    );
  });
  await page.getByLabel("№ чека").fill(sale.receipt_number);
  const response = await filtered;
  expect(response.ok()).toBe(true);
  const sales = (await response.json()) as { items: { id: string }[] };
  const index = sales.items.findIndex((item) => item.id === sale.sale_id);
  expect(index).toBeGreaterThanOrEqual(0);
  await page
    .getByRole("button", { name: `Открыть чек № ${sale.receipt_number}`, exact: true })
    .nth(index)
    .click();
  await page
    .getByRole("dialog", { name: `Чек № ${sale.receipt_number}`, exact: true })
    .getByRole("button", { name: "Оформить возврат", exact: true })
    .click();
}

function persistedFacts(parentId: string, refundId: string, catalogId: string): unknown {
  // SELECT only, against the launcher's disposable E2E database.
  for (const value of [parentId, refundId, catalogId]) {
    expect(value).toMatch(/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i);
  }
  const sql = `SELECT json_build_object(
    'refunds', (SELECT count(*) FROM pos_physical_cash_refund WHERE parent_sale_id='${parentId}'),
    'payments', (SELECT count(*) FROM sale_payment WHERE sale_id='${refundId}'),
    'cash', (SELECT sum(amount)::text FROM sale_payment WHERE sale_id='${refundId}' AND payment_method='cash'),
    'parent_status', (SELECT status FROM sale WHERE id='${parentId}'),
    'remaining', (SELECT sum(l.closed_packs * 20 + COALESCE(
      (SELECT sum(p.remaining_units) FROM unit_stock_opened_pack_v2 p WHERE p.lot_id=l.id), 0))
      FROM unit_stock_lot_v2 l WHERE l.catalog_id='${catalogId}')
  );`;
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
  ) as unknown;
}

test("refunds units, blister and pack once after a lost response and reload", async ({ page }) => {
  test.setTimeout(150_000);
  clearLoginRateLimit(OWNER.email);
  const anonymous = await request.newContext();
  const tokens = await apiLogin(anonymous, OWNER);
  const api = await apiContext(tokens.access_token);
  try {
    const branch = await seedBranch(api, uniqueName("Physical refund branch"));
    const register = await seedRegister(api, branch.id, uniqueName("Physical refund register"));
    const medicine = await seedCatalogItem(api, uniqueName("Physical refund medicine"), "10.00");
    expect(
      (
        await api.patch(`catalog/${medicine.id}`, {
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
    await receivePhysicalStock(api, branch.id, medicine.id);
    await page.setViewportSize({ width: 1366, height: 768 });
    await loginInBrowser(page, OWNER);
    await page.goto("/pos");
    await page.getByLabel(/^Касса$/).selectOption({ label: register.name });
    await page.getByLabel("Наличные в кассе на начало смены").fill("100");
    await page.getByRole("button", { name: "Открыть смену", exact: true }).click();
    await expect(page.getByText("Смена открыта")).toBeVisible();
    const saleId = await addPhysicalFormats(page, medicine.brand_name);
    const cash = page.getByRole("region", { name: "Оплата наличными", exact: true });
    await cash.getByLabel("Получено наличными, TJS").fill("20");
    const checkout = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        new URL(response.url()).pathname === `/api/v1/sales/${saleId}/physical-cash/complete`,
    );
    await cash.getByRole("button", { name: "Завершить продажу", exact: true }).click();
    const checkoutResponse = await checkout;
    expect(checkoutResponse.ok()).toBe(true);
    const sale = (await checkoutResponse.json()) as CheckoutResult;
    expect(sale.total_amount).toBe("16.00");
    await expect(
      page.getByText(`Чек № ${sale.receipt_number} оформлен`, { exact: true }),
    ).toBeVisible();
    await page.goto("/sales");
    await openRefund(page, sale);
    const dialog = page.getByRole("dialog", { name: "Возврат лекарства", exact: true });
    const checkboxes = dialog.getByRole("checkbox", { name: medicine.brand_name, exact: true });
    await expect(checkboxes).toHaveCount(3);
    for (const [index, format, quantity] of [
      [0, "unit", "2"],
      [1, "blister", "1"],
      [2, "pack", "1"],
    ] as const) {
      await checkboxes.nth(index).check();
      await dialog.getByLabel("Что возвращают", { exact: true }).nth(index).selectOption(format);
      await dialog.getByLabel("Количество", { exact: true }).nth(index).fill(quantity);
    }
    const reason = dialog.getByLabel(/^Причина возврата/);
    if (await reason.count()) {
      await reason.selectOption("customer_cancelled");
      await dialog.getByLabel(/^Пояснение/).fill("Проверка возврата в изолированном E2E");
    }
    const quoteResponse = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        new URL(response.url()).pathname === `/api/v1/sales/${saleId}/physical-cash-refunds/quote`,
    );
    await expect(
      dialog.getByRole("button", { name: "Оформить возврат наличными", exact: true }),
    ).toBeDisabled();
    await dialog.getByRole("button", { name: "Рассчитать возврат", exact: true }).click();
    const quote = await quoteResponse;
    expect(quote.ok()).toBe(true);
    expect(await quote.json()).toMatchObject({
      total_amount: "16.00",
      currency: "TJS",
      lines: [
        { sale_format: "unit", quantity: 2, base_units: 2, net_amount: "1.00" },
        { sale_format: "blister", quantity: 1, base_units: 10, net_amount: "5.00" },
        { sale_format: "pack", quantity: 1, base_units: 20, net_amount: "10.00" },
      ],
    });
    await expect(dialog.getByText("16.00 TJS", { exact: true })).toBeVisible();

    let completed: RefundResult | null = null;
    let blockStatus = false;
    const submitted: string[] = [];
    await page.route(
      `**/api/v1/sales/${saleId}/physical-cash-refunds/operations/*`,
      async (route) => {
        if (blockStatus) await route.abort("failed");
        else await route.continue();
      },
    );
    await page.route(`**/api/v1/sales/${saleId}/physical-cash-refunds/complete`, async (route) => {
      submitted.push((route.request().postDataJSON() as { operation_id: string }).operation_id);
      const response = await route.fetch();
      expect(response.ok()).toBe(true);
      completed = (await response.json()) as RefundResult;
      // The server committed the cash payout; only the browser's response is lost.
      blockStatus = true;
      await route.abort("failed");
    });
    await dialog.getByRole("button", { name: "Оформить возврат наличными", exact: true }).click();
    await expect.poll(() => completed !== null).toBe(true);
    const refund = completed as RefundResult | null;
    if (!refund) throw new Error("Refund completion response was not observed");
    expect(refund).toMatchObject({
      state: "completed",
      parent_sale_id: saleId,
      total_amount: "16.00",
    });
    expect(refund.sale_id).not.toBe(saleId);
    await expect(
      dialog.getByRole("heading", { name: "Проверяем ранее отправленный возврат" }),
    ).toBeVisible();

    // Even a closed shift must not turn status recovery into a second payout.
    expect(
      (
        await api.post(`shifts/${sale.shift_id}/close`, {
          data: { closing_cash_actual: "100.00" },
        })
      ).ok(),
    ).toBe(true);
    await page.reload();
    await openRefund(page, sale);
    await expect(
      dialog.getByRole("heading", { name: "Проверяем ранее отправленный возврат" }),
    ).toBeVisible();
    // Wait for the failed mount-time status request before starting manual recovery.
    await expect(dialog.getByRole("alert")).toBeVisible();
    const checkResult = dialog.getByRole("button", { name: "Проверить результат", exact: true });
    await expect(checkResult).toBeEnabled();
    blockStatus = false;
    await checkResult.click();
    await expect(
      dialog.getByRole("heading", { name: "Возврат оформлен", exact: true }),
    ).toBeVisible();
    await expect(dialog.getByText("16.00 TJS", { exact: true })).toBeVisible();
    expect(submitted).toEqual([refund.operation_id]);
    const statusPath = `sales/${saleId}/physical-cash-refunds/operations/${refund.operation_id}`;
    for (let attempt = 0; attempt < 2; attempt += 1) {
      const status = await api.get(statusPath);
      expect(status.ok()).toBe(true);
      expect(await status.json()).toEqual(refund);
    }
    await dialog.getByRole("button", { name: "Открыть чек возврата", exact: true }).click();
    const receipt = page.getByRole("dialog", {
      name: `Чек № ${refund.receipt.receipt_number}`,
      exact: true,
    });
    await expect(receipt.getByText("Чек возврата", { exact: true })).toBeVisible();
    await expect(
      receipt.getByRole("button", { name: "← Открыть исходную продажу", exact: true }),
    ).toBeVisible();
    expect(submitted).toEqual([refund.operation_id]);
    expect(persistedFacts(saleId, refund.sale_id, medicine.id)).toEqual({
      refunds: 1,
      payments: 1,
      cash: "16.00",
      parent_status: "completed",
      remaining: 48,
    });
  } finally {
    await api.dispose();
    await anonymous.dispose();
  }
});
