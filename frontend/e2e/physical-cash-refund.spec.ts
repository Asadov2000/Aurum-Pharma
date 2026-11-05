import { request, test, type APIRequestContext, type Page } from "@playwright/test";

import {
  type MixedRefundCompleted,
  type MixedZeroReturnPrepared,
} from "../src/features/sales/mixedRefund";

import { dockerExec, E2E_POSTGRES_CONTAINER, E2E_POSTGRES_DB } from "./docker";
import {
  addPosItemToCart,
  apiContext,
  apiLogin,
  catalogSearchKey,
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

interface MixedCashResult {
  schema_version: 4;
  document_kind: "mixed_checkout";
  branch_id: string;
  sale_id: string;
  operation_id: string;
  shift_id: string;
  receipt_number: string;
  total_amount: string;
  lines: { inventory_kind: string; quantity: string | number; net_amount: string }[];
  payments: { id: string; amount: string; method: string }[];
}

test("recovers mixed cash sale and refund after response loss and closing their shifts", async ({
  page,
}) => {
  test.setTimeout(210_000);
  clearLoginRateLimit(OWNER.email);
  const anonymous = await request.newContext();
  const tokens = await apiLogin(anonymous, OWNER);
  const api = await apiContext(tokens.access_token);
  try {
    const branch = await seedBranch(api, uniqueName("Mixed cash branch"));
    const register = await seedRegister(api, branch.id, uniqueName("Mixed cash register"));
    const supplier = await seedSupplier(api, uniqueName("Mixed cash supplier"));
    const legacy = await seedCatalogItem(api, uniqueName("Mixed legacy medicine"), "10.00");
    const physical = await seedCatalogItem(api, uniqueName("Mixed unit medicine"), "10.00");
    const expiry = new Date();
    expiry.setUTCDate(expiry.getUTCDate() + 180);
    const batch = await seedAcceptedBatch(api, {
      branchId: branch.id,
      supplierId: supplier.id,
      catalogId: legacy.id,
      qty: "4",
      purchasePrice: "5.00",
      salePrice: "10.00",
      expiresAt: expiry.toISOString().slice(0, 10),
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
    await receivePhysicalStock(api, branch.id, physical.id);
    await page.setViewportSize({ width: 1366, height: 768 });
    await loginInBrowser(page, OWNER);
    await page.goto("/pos");
    await page.getByLabel(/^Касса$/).selectOption({ label: register.name });
    await page.getByLabel("Наличные в кассе на начало смены").fill("100");
    await page.getByRole("button", { name: "Открыть смену", exact: true }).click();
    await expect(page.getByText("Смена открыта")).toBeVisible();
    await addPosItemToCart(page, {
      brandName: legacy.brand_name,
      qty: "1",
      expectedCartItems: 1,
    });
    // The physical editor must remain reachable after adding an ordinary pack.
    await page.getByRole("button", { name: "Выбрать упаковку, блистер или штуки" }).click();
    const editor = page.getByRole("region", { name: "Продажа упаковками, блистерами и поштучно" });
    await editor.getByLabel("Название лекарства").fill(catalogSearchKey(physical.brand_name));
    await editor.getByRole("option", { name: new RegExp(physical.brand_name) }).click();
    await editor.getByRole("radio", { name: "Поштучно", exact: true }).click();
    await editor.getByLabel("Количество, шт.", { exact: true }).fill("2");
    const added = page.waitForResponse(
      (response) =>
        response.request().method() === "PUT" &&
        /\/sales\/[^/]+\/physical-cart\/items\/[^/]+$/.test(new URL(response.url()).pathname),
    );
    await editor.getByRole("button", { name: "Добавить в корзину", exact: true }).click();
    const addedResponse = await added;
    expect(addedResponse.ok()).toBe(true);
    const { sale_id: saleId } = (await addedResponse.json()) as { sale_id: string };
    const cash = page.getByRole("region", { name: "Оплата наличными", exact: true });
    await expect(cash.getByText("11.00 TJS", { exact: true })).toBeVisible();
    await cash.getByLabel("Получено наличными, TJS").fill("20");

    const submitted: string[] = [];
    const wrongPaymentPaths: string[] = [];
    let completed: MixedCashResult | null = null;
    let blockStatus = false;
    page.on("request", (req) => {
      const path = new URL(req.url()).pathname;
      if (
        req.method() === "POST" &&
        (path.startsWith(`/api/v1/sales/${saleId}/physical-cash/`) ||
          [
            `/api/v1/sales/${saleId}/checkout`,
            `/api/v1/sales/${saleId}/complete`,
            `/api/v1/sales/${saleId}/payments`,
          ].includes(path))
      )
        wrongPaymentPaths.push(path);
    });
    await page.route(`**/api/v1/sales/${saleId}/mixed-cash/operations/*`, async (route) => {
      if (blockStatus) await route.abort("failed");
      else await route.continue();
    });
    await page.route(`**/api/v1/sales/${saleId}/mixed-cash/complete`, async (route) => {
      submitted.push((route.request().postDataJSON() as { operation_id: string }).operation_id);
      const response = await route.fetch();
      expect(response.ok()).toBe(true);
      completed = (await response.json()) as MixedCashResult;
      blockStatus = true;
      await route.abort("failed");
    });
    await cash.getByRole("button", { name: "Завершить продажу", exact: true }).click();
    await expect.poll(() => completed !== null).toBe(true);
    const result = completed as MixedCashResult | null;
    if (!result) throw new Error("Mixed cash completion response was not observed");
    expect(result).toMatchObject({
      schema_version: 4,
      document_kind: "mixed_checkout",
      sale_id: saleId,
      total_amount: "11.00",
      lines: [
        { inventory_kind: "legacy_pack", quantity: "1.000", net_amount: "10.00" },
        { inventory_kind: "physical_unit", quantity: 2, net_amount: "1.00" },
      ],
      payments: [{ method: "cash", amount: "11.00" }],
    });
    const recoveryEntries = () =>
      page.evaluate(() =>
        Object.entries(localStorage).filter(([key]) =>
          key.startsWith("aurum:mixed-cash-recovery:v1:"),
        ),
      );
    await expect.poll(async () => (await recoveryEntries()).length).toBe(1);
    await expect(
      cash.getByRole("button", { name: "Проверить и завершить оплату", exact: true }),
    ).toBeEnabled();
    const pendingBytes = (await recoveryEntries())[0]?.[1];
    expect(pendingBytes).toBeDefined();
    expect(JSON.parse(pendingBytes ?? "null")).toMatchObject({
      request: { operation_id: result.operation_id },
    });
    await expect(
      page.getByText(`Чек № ${result.receipt_number} оформлен`, { exact: true }),
    ).toHaveCount(0);
    expect(
      (
        await api.post(`shifts/${result.shift_id}/close`, {
          data: { closing_cash_actual: "111.00" },
        })
      ).ok(),
    ).toBe(true);
    await page.reload();
    const recover = cash.getByRole("button", { name: "Проверить и завершить оплату", exact: true });
    await expect(cash.getByRole("alert")).toBeVisible();
    await expect(recover).toBeEnabled();
    expect((await recoveryEntries())[0]?.[1]).toBe(pendingBytes);
    blockStatus = false;
    await recover.click();
    await expect(
      page.getByText(`Чек № ${result.receipt_number} оформлен`, { exact: true }),
    ).toBeVisible();
    await expect.poll(async () => (await recoveryEntries()).length).toBe(0);
    expect(submitted).toEqual([result.operation_id]);
    expect(wrongPaymentPaths).toEqual([]);
    const status = await api.get(`sales/${saleId}/mixed-cash/operations/${result.operation_id}`);
    expect(status.ok()).toBe(true);
    expect(await status.json()).toMatchObject({ state: "completed", result });
    const savedBatch = await api.get(`batches/${batch.id}`);
    expect(savedBatch.ok()).toBe(true);
    expect(await savedBatch.json()).toMatchObject({ qty_remaining: "3.000" });
    // This helper runs SELECT only in the launcher's disposable E2E database.
    for (const value of [saleId, physical.id])
      expect(value).toMatch(/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i);
    const sql = `SELECT json_build_object(
      'markers', (SELECT count(*) FROM pos_mixed_checkout WHERE sale_id='${saleId}'),
      'payments', (SELECT count(*) FROM sale_payment WHERE sale_id='${saleId}'),
      'movements', (SELECT count(*) FROM batch_movement WHERE source_table='sale' AND source_id='${saleId}' AND movement_type='sale'),
      'commands', (SELECT count(*) FROM unit_stock_cart_command_v2 WHERE mixed_checkout_sale_id='${saleId}'),
      'receipt_version', (SELECT receipt_snapshot->>'schema_version' FROM sale WHERE id='${saleId}'),
      'remaining', (SELECT sum(l.closed_packs*20+COALESCE((SELECT sum(p.remaining_units) FROM unit_stock_opened_pack_v2 p WHERE p.lot_id=l.id),0)) FROM unit_stock_lot_v2 l WHERE l.catalog_id='${physical.id}')
    );`;
    expect(
      JSON.parse(
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
      ) as unknown,
    ).toEqual({
      markers: 1,
      payments: 1,
      movements: 1,
      commands: 1,
      receipt_version: "4",
      remaining: 78,
    });
    await page.getByRole("button", { name: "Печать чека", exact: true }).click();
    await expect(page.getByRole("dialog", { name: "Печать чека", exact: true })).toBeVisible();
    await expect(
      page.locator(".receipt-print").getByText(`1. ${legacy.brand_name}`, { exact: true }),
    ).toBeVisible();
    await expect(
      page.locator(".receipt-print").getByText(`2. ${physical.brand_name}`, { exact: true }),
    ).toBeVisible();
    await verifyMixedRefund(
      page,
      api,
      result,
      register.id,
      legacy.brand_name,
      physical.brand_name,
      physical.id,
    );
    expect(await (await api.get(`batches/${batch.id}`)).json()).toMatchObject({
      qty_remaining: "3.000",
    });
  } finally {
    await api.dispose();
    await anonymous.dispose();
  }
});

async function verifyMixedRefund(
  page: Page,
  api: APIRequestContext,
  sale: MixedCashResult,
  registerId: string,
  legacyName: string,
  physicalName: string,
  physicalId: string,
): Promise<void> {
  const opened = await api.post("shifts/open", {
    data: { register_id: registerId, opening_cash: "111.00" },
  });
  expect(opened.ok()).toBe(true);
  const shift = (await opened.json()) as { id: string };
  await page.goto("/sales");
  await openRefund(page, sale);
  const dialog = page.getByRole("dialog", { name: "Возврат смешанной продажи", exact: true });
  await dialog.getByRole("checkbox", { name: legacyName, exact: true }).check();
  await dialog.getByLabel(`Количество ${legacyName}`, { exact: true }).fill("0.125");
  await dialog.getByRole("checkbox", { name: physicalName, exact: true }).check();
  await dialog.getByLabel(`Количество ${physicalName}`, { exact: true }).fill("1");
  const reason = dialog.getByLabel(/^Причина возврата/);
  if (await reason.count()) {
    await reason.selectOption("customer_cancelled");
    await dialog.getByLabel(/^Пояснение/).fill("Проверка смешанного возврата в изолированном E2E");
  }
  const confirm = dialog.getByRole("button", { name: "Оформить возврат наличными", exact: true });
  await expect(confirm).toBeDisabled();
  await dialog.getByRole("button", { name: "Рассчитать возврат", exact: true }).click();
  await expect(dialog.getByText("1.75 TJS", { exact: true })).toBeVisible();
  await expect(confirm).toBeEnabled();
  let result: MixedRefundCompleted | null = null;
  let blockStatus = false;
  const requests: string[] = [];
  const path = `**/api/v1/sales/${sale.sale_id}/mixed-cash-refunds`;
  await page.route(`${path}/operations/*`, async (route) => {
    if (blockStatus) await route.abort("failed");
    else await route.continue();
  });
  await page.route(`${path}/complete`, async (route) => {
    requests.push((route.request().postDataJSON() as { operation_id: string }).operation_id);
    const response = await route.fetch();
    expect(response.ok()).toBe(true);
    result = (await response.json()) as MixedRefundCompleted;
    blockStatus = true;
    await route.abort("failed");
  });
  await confirm.click();
  await expect.poll(() => result !== null).toBe(true);
  const refund = result as MixedRefundCompleted | null;
  if (!refund) throw new Error("Mixed refund completion response was not observed");
  expect(refund).toMatchObject({
    parent_sale_id: sale.sale_id,
    total_amount: "1.75",
    receipt: {
      schema_version: 4,
      document_kind: "mixed_cash_refund_receipt",
      lines: [
        { inventory_kind: "legacy_pack", quantity: "0.125", net_amount: "1.25" },
        { inventory_kind: "physical_unit", quantity: 1, base_units: 1, net_amount: "0.50" },
      ],
    },
  });
  await expect(
    dialog.getByRole("heading", { name: "Проверяем ранее отправленный возврат" }),
  ).toBeVisible();
  expect(
    (
      await api.post(`shifts/${shift.id}/close`, {
        data: { closing_cash_actual: "109.25" },
      })
    ).ok(),
  ).toBe(true);
  await page.reload();
  await openRefund(page, sale);
  await expect(dialog.getByRole("alert")).toBeVisible();
  const inspect = dialog.getByRole("button", { name: "Проверить результат", exact: true });
  await expect(inspect).toBeEnabled();
  expect(requests).toEqual([refund.operation_id]);
  blockStatus = false;
  await inspect.click();
  await expect(
    dialog.getByRole("heading", { name: "Возврат оформлен", exact: true }),
  ).toBeVisible();
  await dialog.getByRole("button", { name: "Открыть чек возврата", exact: true }).click();
  const receipt = page.getByRole("dialog", {
    name: `Чек № ${refund.receipt.receipt_number}`,
    exact: true,
  });
  await expect(receipt.getByText("Чек возврата", { exact: true })).toBeVisible();
  await expect(receipt.getByText(/Возвращено: 0.125 уп/)).toBeVisible();
  await expect(receipt.getByText(/Возвращено: 1 табл/)).toBeVisible();
  await receipt.getByRole("button", { name: "Печать возврата", exact: true }).click();
  await expect(
    page.locator(".receipt-print").getByText(`1. ${legacyName}`, { exact: true }),
  ).toBeVisible();
  await expect(
    page.locator(".receipt-print").getByText(`2. ${physicalName}`, { exact: true }),
  ).toBeVisible();
  expect(requests).toEqual([refund.operation_id]);
  for (const value of [sale.sale_id, refund.sale_id, physicalId])
    expect(value).toMatch(/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i);
  // SELECT only in the launcher's disposable E2E database; both goods stay in quarantine.
  const sql = `SELECT json_build_object(
    'refunds', (SELECT count(*) FROM pos_mixed_cash_refund WHERE parent_sale_id='${sale.sale_id}'),
    'payments', (SELECT count(*) FROM sale_payment WHERE sale_id='${refund.sale_id}'),
    'cash', (SELECT sum(amount)::text FROM sale_payment WHERE sale_id='${refund.sale_id}' AND payment_method='cash'),
    'remaining', (SELECT sum(l.closed_packs*20+COALESCE((SELECT sum(p.remaining_units) FROM unit_stock_opened_pack_v2 p WHERE p.lot_id=l.id),0)) FROM unit_stock_lot_v2 l WHERE l.catalog_id='${physicalId}')
  );`;
  expect(
    JSON.parse(
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
    ) as unknown,
  ).toEqual({ refunds: 1, payments: 1, cash: "1.75", remaining: 78 });
  expect(
    await page.evaluate(() =>
      Object.keys(localStorage).filter((key) => key.startsWith("sales:mixedRefund:v1:")),
    ),
  ).toEqual([]);
  await verifyMixedQuarantineQueue(
    page,
    api,
    sale,
    refund.operation_id,
    legacyName,
    physicalName,
    "0.125",
    false,
  );
}

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

async function verifyQuarantineQueue(
  page: Page,
  api: APIRequestContext,
  medicineName: string,
  operationId: string,
  expectedUnits: number[],
  withoutPayout: boolean,
): Promise<void> {
  const response = await api.get("customer-returns/physical", {
    params: { search: medicineName },
  });
  expect(response.ok()).toBe(true);
  const queue = (await response.json()) as {
    total: number;
    items: { operation_id: string; base_units: number; unit_label: string; intake_kind: string }[];
  };
  expect(queue.total).toBe(expectedUnits.length);
  expect(queue.items.map((item) => item.base_units).sort((a, b) => a - b)).toEqual(expectedUnits);
  expect(queue.items.every((item) => item.operation_id === operationId)).toBe(true);
  expect(queue.items.every((item) => item.unit_label === "табл.")).toBe(true);
  expect(
    queue.items.every(
      (item) => item.intake_kind === (withoutPayout ? "zero_return" : "cash_refund"),
    ),
  ).toBe(true);
  expect(JSON.stringify(queue)).not.toMatch(
    /cost_amount|total_cost|purchase_cost|result_payload|receipt_payload/,
  );

  await page.goto("/batches");
  await page.getByRole("button", { name: "Возвраты покупателей", exact: true }).click();
  await page.getByRole("button", { name: "Физические возвраты", exact: true }).click();
  const panel = page.getByRole("region", { name: "Физические возвраты в карантине", exact: true });
  const filtered = page.waitForResponse((result) => {
    const url = new URL(result.url());
    return (
      url.pathname === "/api/v1/customer-returns/physical" &&
      url.searchParams.get("search") === medicineName
    );
  });
  await panel.getByLabel("Поиск", { exact: true }).fill(medicineName);
  expect((await filtered).ok()).toBe(true);
  const table = panel.getByRole("table", { name: "Очередь физических возвратов" });
  await expect(table.getByRole("row")).toHaveCount(expectedUnits.length + 1);
  for (const units of expectedUnits) {
    await expect(table.getByRole("cell", { name: `${units} табл.`, exact: true })).toBeVisible();
  }
  await expect(panel.getByRole("button", { name: "Принять решение", exact: true })).toHaveCount(0);
  if (withoutPayout)
    await expect(table.getByText("Принят без выплаты", { exact: true })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  const cards = panel.getByRole("article", {
    name: `${medicineName}, физический возврат`,
    exact: true,
  });
  await expect(cards).toHaveCount(expectedUnits.length);
  await expect(cards.first()).toBeVisible();
}

async function verifyMixedQuarantineQueue(
  page: Page,
  api: APIRequestContext,
  sale: Pick<MixedCashResult, "sale_id" | "receipt_number" | "branch_id">,
  operationId: string,
  legacyName: string,
  physicalName: string,
  legacyQuantity: string,
  withoutPayout: boolean,
): Promise<void> {
  const response = await api.get("customer-returns/mixed", {
    params: { search: sale.receipt_number, branch_id: sale.branch_id },
  });
  expect(response.ok()).toBe(true);
  const queue = (await response.json()) as {
    total: number;
    items: {
      operation_id: string;
      parent_sale_id: string;
      inventory_kind: string;
      legacy_quantity: string | null;
      base_units: number | null;
      unit_label: string;
      intake_kind: string;
      return_sale_id: string | null;
      return_receipt_number: string | null;
    }[];
  };
  expect(queue.total).toBe(2);
  expect(
    queue.items.every(
      (item) => item.operation_id === operationId && item.parent_sale_id === sale.sale_id,
    ),
  ).toBe(true);
  expect(queue.items.find((item) => item.inventory_kind === "legacy_pack")).toMatchObject({
    legacy_quantity: legacyQuantity,
    base_units: null,
    unit_label: "уп.",
  });
  expect(queue.items.find((item) => item.inventory_kind === "physical_unit")).toMatchObject({
    legacy_quantity: null,
    base_units: 1,
    unit_label: "табл.",
  });
  expect(
    queue.items.every(
      (item) => item.intake_kind === (withoutPayout ? "zero_return" : "cash_refund"),
    ),
  ).toBe(true);
  if (withoutPayout)
    expect(
      queue.items.every(
        (item) => item.return_sale_id === null && item.return_receipt_number === null,
      ),
    ).toBe(true);
  expect(JSON.stringify(queue)).not.toMatch(
    /cost_amount|total_cost|purchase_cost|result_payload|receipt_payload/,
  );

  const writes: string[] = [];
  const observe = (req: import("@playwright/test").Request): void => {
    if (
      req.method() !== "GET" &&
      new URL(req.url()).pathname.startsWith("/api/v1/customer-returns")
    )
      writes.push(req.method());
  };
  page.on("request", observe);
  try {
    await page.setViewportSize({ width: 1366, height: 768 });
    await page.goto("/batches");
    await page.getByRole("button", { name: "Возвраты покупателей", exact: true }).click();
    await page.getByRole("button", { name: "Смешанные возвраты", exact: true }).click();
    const panel = page.getByRole("region", { name: "Смешанные возвраты в карантине", exact: true });
    await panel.getByLabel("Точка", { exact: true }).selectOption(sale.branch_id);
    const filtered = page.waitForResponse((result) => {
      const url = new URL(result.url());
      return (
        url.pathname === "/api/v1/customer-returns/mixed" &&
        url.searchParams.get("branch_id") === sale.branch_id &&
        url.searchParams.get("search") === sale.receipt_number
      );
    });
    await panel.getByLabel("Поиск", { exact: true }).fill(sale.receipt_number);
    expect((await filtered).ok()).toBe(true);
    const table = panel.getByRole("table", { name: "Очередь смешанных возвратов", exact: true });
    await expect(table.getByRole("row")).toHaveCount(3);
    await expect(
      table.getByRole("cell", {
        name: `${Number(legacyQuantity).toLocaleString("ru-RU", { maximumFractionDigits: 3 })} уп.`,
        exact: true,
      }),
    ).toBeVisible();
    await expect(table.getByRole("cell", { name: "1 табл.", exact: true })).toBeVisible();
    if (withoutPayout)
      await expect(table.getByText("Принят без выплаты", { exact: true })).toHaveCount(2);
    await expect(table.getByRole("button", { name: "Принять решение", exact: true })).toHaveCount(
      2,
    );
    for (const button of await table
      .getByRole("button", { name: "Принять решение", exact: true })
      .all())
      await expect(button).toBeEnabled();
    await page.setViewportSize({ width: 390, height: 844 });
    for (const name of [legacyName, physicalName])
      await expect(
        panel.getByRole("article", { name: `${name}, смешанный возврат`, exact: true }),
      ).toBeVisible();
    expect(writes).toEqual([]);
  } finally {
    page.off("request", observe);
  }
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

async function openRefund(
  page: Page,
  sale: Pick<CheckoutResult, "sale_id" | "receipt_number">,
): Promise<void> {
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
    await verifyQuarantineQueue(
      page,
      api,
      medicine.brand_name,
      refund.operation_id,
      [2, 10, 20],
      false,
    );
  } finally {
    await api.dispose();
    await anonymous.dispose();
  }
});

interface ZeroReturnResult {
  kind: "physical_zero_return";
  state: "prepared_quarantine";
  operation_id: string;
  parent_sale_id: string;
  total_amount: string;
  lines: { line_id: string; base_units: number; offset: number }[];
}

interface ZeroReturnFacts {
  zero_markers: number;
  journal_commands: number;
  sales: number;
  payments: number;
  original_hash: string;
  receipt_counter_hash: string;
  remaining: number;
}

function zeroReturnFacts(parentId: string, catalogId: string): ZeroReturnFacts {
  for (const value of [parentId, catalogId]) {
    expect(value).toMatch(/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i);
  }
  // Read-only aggregates and fingerprints avoid exposing receipt contents.
  const sql = `SELECT json_build_object(
    'zero_markers', (SELECT count(*) FROM pos_physical_zero_return WHERE parent_sale_id='${parentId}'),
    'journal_commands', (SELECT count(*) FROM pos_physical_return_command WHERE parent_sale_id='${parentId}'),
    'sales', (SELECT count(*) FROM sale WHERE branch_id=(SELECT branch_id FROM sale WHERE id='${parentId}')),
    'payments', (SELECT count(*) FROM sale_payment p JOIN sale s ON s.id=p.sale_id
      WHERE s.branch_id=(SELECT branch_id FROM sale WHERE id='${parentId}')),
    'original_hash', (SELECT md5(to_jsonb(s)::text) FROM sale s WHERE id='${parentId}'),
    'receipt_counter_hash', (SELECT md5(jsonb_agg(to_jsonb(c) ORDER BY to_jsonb(c)::text)::text)
      FROM register_receipt_counter c WHERE c.register_id=(SELECT register_id FROM sale WHERE id='${parentId}')),
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
  ) as ZeroReturnFacts;
}

async function completeDiscountedPack(
  api: APIRequestContext,
  registerId: string,
  catalogId: string,
): Promise<CheckoutResult> {
  expect(
    (
      await api.post("shifts/open", {
        data: { register_id: registerId, opening_cash: "0.00" },
      })
    ).ok(),
  ).toBe(true);
  const created = await api.post("sales", {
    data: { operation_id: crypto.randomUUID(), register_id: registerId },
  });
  expect(created.ok()).toBe(true);
  const sale = (await created.json()) as { id: string };
  const item = await api.put(`sales/${sale.id}/physical-cart/items/${crypto.randomUUID()}`, {
    data: {
      catalog_id: catalogId,
      position: 1,
      sale_format: "pack",
      quantity: 1,
      discount_amount: "9.99",
    },
  });
  expect(item.ok()).toBe(true);
  const quoted = await api.get(`sales/${sale.id}/physical-cart/quote`);
  expect(quoted.ok()).toBe(true);
  const quote = (await quoted.json()) as { revision_hash: string; quote: { total_amount: string } };
  expect(quote.quote.total_amount).toBe("0.01");
  const confirmation = {
    operation_id: crypto.randomUUID(),
    sale_id: sale.id,
    expected_revision_hash: quote.revision_hash,
    expected_total_amount: "0.01",
    cash_tendered: "0.01",
  };
  expect(
    (await api.post(`sales/${sale.id}/physical-cash/intent`, { data: confirmation })).ok(),
  ).toBe(true);
  const completed = await api.post(`sales/${sale.id}/physical-cash/complete`, {
    data: confirmation,
  });
  expect(completed.ok()).toBe(true);
  return (await completed.json()) as CheckoutResult;
}

test("receives a zero-amount unit without a shift or second operation after response loss", async ({
  page,
}) => {
  test.setTimeout(150_000);
  clearLoginRateLimit(OWNER.email);
  const anonymous = await request.newContext();
  const tokens = await apiLogin(anonymous, OWNER);
  const api = await apiContext(tokens.access_token);
  try {
    const branch = await seedBranch(api, uniqueName("Zero return branch"));
    const register = await seedRegister(api, branch.id, uniqueName("Zero return register"));
    const medicine = await seedCatalogItem(api, uniqueName("Zero return medicine"), "10.00");
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
    const sale = await completeDiscountedPack(api, register.id, medicine.id);
    expect(sale.total_amount).toBe("0.01");
    expect(
      (
        await api.post(`shifts/${sale.shift_id}/close`, {
          data: { closing_cash_actual: "0.01" },
        })
      ).ok(),
    ).toBe(true);
    const before = zeroReturnFacts(sale.sale_id, medicine.id);
    expect(before).toMatchObject({
      zero_markers: 0,
      journal_commands: 0,
      sales: 1,
      payments: 1,
      remaining: 60,
    });

    await page.setViewportSize({ width: 1366, height: 768 });
    await loginInBrowser(page, OWNER);
    await page.goto("/sales");
    await openRefund(page, sale);
    const dialog = page.getByRole("dialog", { name: "Возврат лекарства", exact: true });
    await dialog.getByRole("checkbox", { name: medicine.brand_name, exact: true }).check();
    await dialog.getByLabel("Что возвращают", { exact: true }).selectOption("unit");
    await dialog.getByLabel("Количество", { exact: true }).fill("1");
    const reason = dialog.getByLabel(/^Причина возврата/);
    if (await reason.count()) {
      await reason.selectOption("customer_cancelled");
      await dialog.getByLabel(/^Пояснение/).fill("Изолированная проверка приёма без выплаты");
    }
    const quoting = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        new URL(response.url()).pathname ===
          `/api/v1/sales/${sale.sale_id}/physical-cash-refunds/quote`,
    );
    await dialog.getByRole("button", { name: "Рассчитать возврат", exact: true }).click();
    const quoteResponse = await quoting;
    expect(quoteResponse.ok()).toBe(true);
    expect(await quoteResponse.json()).toMatchObject({ total_amount: "0.00", currency: "TJS" });
    const receive = dialog.getByRole("button", {
      name: "Принять в карантин без выплаты",
      exact: true,
    });
    await expect(receive).toBeEnabled();

    let completed: ZeroReturnResult | null = null;
    let blockStatus = false;
    const submitted: string[] = [];
    await page.route(
      `**/api/v1/sales/${sale.sale_id}/physical-zero-returns/operations/*`,
      async (route) => {
        if (blockStatus) await route.abort("failed");
        else await route.continue();
      },
    );
    await page.route(
      `**/api/v1/sales/${sale.sale_id}/physical-zero-returns/complete`,
      async (route) => {
        submitted.push((route.request().postDataJSON() as { operation_id: string }).operation_id);
        const response = await route.fetch();
        expect(response.ok()).toBe(true);
        completed = (await response.json()) as ZeroReturnResult;
        blockStatus = true;
        await route.abort("failed");
      },
    );
    await receive.click();
    await expect.poll(() => completed !== null).toBe(true);
    const result = completed as ZeroReturnResult | null;
    if (!result) throw new Error("Zero return completion response was not observed");
    expect(result).toMatchObject({
      kind: "physical_zero_return",
      state: "prepared_quarantine",
      parent_sale_id: sale.sale_id,
      total_amount: "0.00",
      lines: [{ base_units: 1, offset: 0 }],
    });
    expect(result).not.toHaveProperty("receipt");
    expect(result).not.toHaveProperty("sale_id");
    expect(result).not.toHaveProperty("payment_id");
    await expect(
      dialog.getByRole("heading", { name: "Проверяем ранее отправленный возврат" }),
    ).toBeVisible();

    const recoveryWrites: string[] = [];
    page.on("request", (request) => {
      const path = new URL(request.url()).pathname;
      if (
        request.method() !== "GET" &&
        path.startsWith(`/api/v1/sales/${sale.sale_id}/physical-`)
      ) {
        recoveryWrites.push(`${request.method()} ${path}`);
      }
    });
    await page.reload();
    await openRefund(page, sale);
    await expect(
      dialog.getByRole("heading", { name: "Проверяем ранее отправленный возврат" }),
    ).toBeVisible();
    await expect(dialog.getByRole("alert")).toBeVisible();
    const check = dialog.getByRole("button", { name: "Проверить результат", exact: true });
    await expect(check).toBeEnabled();
    blockStatus = false;
    const recovering = page.waitForResponse(
      (response) =>
        response.request().method() === "GET" &&
        new URL(response.url()).pathname ===
          `/api/v1/sales/${sale.sale_id}/physical-zero-returns/operations/${result.operation_id}`,
    );
    await check.click();
    const recovery = await recovering;
    expect(recovery.ok()).toBe(true);
    expect(await recovery.json()).toEqual(result);
    await expect(
      dialog.getByRole("heading", { name: "Товар принят в карантин", exact: true }),
    ).toBeVisible();
    await expect(
      dialog.getByRole("button", { name: "Открыть чек возврата", exact: true }),
    ).toHaveCount(0);
    expect(submitted).toEqual([result.operation_id]);
    expect(zeroReturnFacts(sale.sale_id, medicine.id)).toEqual({
      ...before,
      zero_markers: 1,
      journal_commands: 1,
    });
    await dialog.getByRole("button", { name: "Готово", exact: true }).click();
    await expect(dialog).not.toBeVisible();
    expect(submitted).toEqual([result.operation_id]);
    expect(recoveryWrites).toEqual([]);
    await verifyQuarantineQueue(page, api, medicine.brand_name, result.operation_id, [1], true);
  } finally {
    await api.dispose();
    await anonymous.dispose();
  }
});

function mixedZeroFacts(parentId: string, physicalId: string): unknown {
  for (const value of [parentId, physicalId])
    expect(value).toMatch(/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i);
  // Only the launcher's disposable database is queried; runtime data is never used.
  const sql = `SELECT json_build_object(
    'markers', (SELECT count(*) FROM pos_mixed_zero_return WHERE parent_sale_id='${parentId}'),
    'journal', (SELECT count(*) FROM pos_mixed_return_command WHERE parent_sale_id='${parentId}'),
    'sales', (SELECT count(*) FROM sale WHERE id='${parentId}' OR parent_sale_id='${parentId}'),
    'payments', (SELECT count(*) FROM sale_payment p JOIN sale s ON s.id=p.sale_id WHERE s.id='${parentId}' OR s.parent_sale_id='${parentId}'),
    'physical_remaining', (SELECT sum(l.closed_packs*20+COALESCE((SELECT sum(p.remaining_units) FROM unit_stock_opened_pack_v2 p WHERE p.lot_id=l.id),0)) FROM unit_stock_lot_v2 l WHERE l.catalog_id='${physicalId}')
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

test("receives zero-value mixed goods once without a shift or payment after a lost response", async ({
  page,
}) => {
  test.setTimeout(150_000);
  clearLoginRateLimit(OWNER.email);
  const anonymous = await request.newContext();
  const tokens = await apiLogin(anonymous, OWNER);
  const api = await apiContext(tokens.access_token);
  try {
    const branch = await seedBranch(api, uniqueName("Mixed zero branch"));
    const register = await seedRegister(api, branch.id, uniqueName("Mixed zero register"));
    const supplier = await seedSupplier(api, uniqueName("Mixed zero supplier"));
    const legacy = await seedCatalogItem(api, uniqueName("Mixed zero packs"), "0.01");
    const physical = await seedCatalogItem(api, uniqueName("Mixed zero tablets"), "10.00");
    const expiry = new Date();
    expiry.setUTCDate(expiry.getUTCDate() + 180);
    const batch = await seedAcceptedBatch(api, {
      branchId: branch.id,
      supplierId: supplier.id,
      catalogId: legacy.id,
      qty: "4",
      purchasePrice: "0.01",
      salePrice: "0.01",
      expiresAt: expiry.toISOString().slice(0, 10),
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
    await receivePhysicalStock(api, branch.id, physical.id);
    expect(
      (
        await api.post("shifts/open", {
          data: { register_id: register.id, opening_cash: "0.00" },
        })
      ).ok(),
    ).toBe(true);
    const draftResponse = await api.post("sales", {
      data: { operation_id: crypto.randomUUID(), register_id: register.id },
    });
    expect(draftResponse.ok()).toBe(true);
    const draft = (await draftResponse.json()) as { id: string };
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
            position: 2,
            sale_format: "pack",
            quantity: 1,
            discount_amount: "9.99",
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
    expect(quote.quote.total_amount).toBe("0.02");
    const confirmation = {
      operation_id: crypto.randomUUID(),
      sale_id: draft.id,
      expected_revision_hash: quote.revision_hash,
      expected_total_amount: "0.02",
      cash_tendered: "0.02",
    };
    expect(
      (await api.post(`sales/${draft.id}/mixed-cash/intent`, { data: confirmation })).ok(),
    ).toBe(true);
    const paid = await api.post(`sales/${draft.id}/mixed-cash/complete`, { data: confirmation });
    expect(paid.ok()).toBe(true);
    const sale = (await paid.json()) as MixedCashResult;
    expect(
      (
        await api.post(`shifts/${sale.shift_id}/close`, {
          data: { closing_cash_actual: "0.02" },
        })
      ).ok(),
    ).toBe(true);
    const before = mixedZeroFacts(sale.sale_id, physical.id);
    expect(before).toEqual({
      markers: 0,
      journal: 0,
      sales: 1,
      payments: 1,
      physical_remaining: 60,
    });

    await loginInBrowser(page, OWNER);
    await page.goto("/sales");
    await openRefund(page, sale);
    const dialog = page.getByRole("dialog", { name: "Возврат смешанной продажи", exact: true });
    await dialog.getByRole("checkbox", { name: legacy.brand_name, exact: true }).check();
    await dialog.getByLabel(`Количество ${legacy.brand_name}`, { exact: true }).fill("0.001");
    await dialog.getByRole("checkbox", { name: physical.brand_name, exact: true }).check();
    await dialog.getByLabel(`Количество ${physical.brand_name}`, { exact: true }).fill("1");
    if (await dialog.getByLabel(/^Причина возврата/).count()) {
      await dialog.getByLabel(/^Причина возврата/).selectOption("customer_cancelled");
      await dialog
        .getByLabel(/^Пояснение/)
        .fill("Изолированная проверка смешанного приёма без выплаты");
    }
    await dialog.getByRole("button", { name: "Рассчитать возврат", exact: true }).click();
    await expect(dialog.getByText("0.00 TJS", { exact: true })).toBeVisible();
    const receive = dialog.getByRole("button", {
      name: "Принять в карантин без выплаты",
      exact: true,
    });
    await expect(receive).toBeEnabled();
    let completed: MixedZeroReturnPrepared | null = null;
    let blockStatus = false;
    const submissions: string[] = [];
    const prefix = `**/api/v1/sales/${sale.sale_id}/mixed-zero-returns`;
    await page.route(`${prefix}/operations/*`, async (route) => {
      if (blockStatus) await route.abort("failed");
      else await route.continue();
    });
    await page.route(`${prefix}/complete`, async (route) => {
      submissions.push((route.request().postDataJSON() as { operation_id: string }).operation_id);
      const response = await route.fetch();
      expect(response.ok()).toBe(true);
      completed = (await response.json()) as MixedZeroReturnPrepared;
      blockStatus = true;
      await route.abort("failed");
    });
    await receive.click();
    await expect.poll(() => completed !== null).toBe(true);
    const result = completed as MixedZeroReturnPrepared | null;
    if (!result) throw new Error("Mixed zero intake response was not observed");
    expect(result).toMatchObject({
      kind: "mixed_zero_return",
      state: "prepared_quarantine",
      total_amount: "0.00",
    });
    expect(result.lines).toMatchObject([
      { inventory_kind: "legacy_pack", quantity: "0.001", offset: "0.000" },
      { inventory_kind: "physical_unit", quantity: 1, base_units: 1, offset: 0 },
    ]);
    for (const field of ["sale_id", "receipt", "payment_id", "physical_cost_amount"])
      expect(result).not.toHaveProperty(field);
    await expect(
      dialog.getByRole("heading", { name: "Проверяем ранее отправленный возврат" }),
    ).toBeVisible();
    const recoveryWrites: string[] = [];
    page.on("request", (req) => {
      const path = new URL(req.url()).pathname;
      if (req.method() !== "GET" && path.startsWith(`/api/v1/sales/${sale.sale_id}/mixed-`))
        recoveryWrites.push(`${req.method()} ${path}`);
    });
    await page.reload();
    await openRefund(page, sale);
    await expect(dialog.getByRole("alert")).toBeVisible();
    const check = dialog.getByRole("button", { name: "Проверить результат", exact: true });
    await expect(check).toBeEnabled();
    blockStatus = false;
    await check.click();
    await expect(
      dialog.getByRole("heading", { name: "Товар принят в карантин", exact: true }),
    ).toBeVisible();
    expect(mixedZeroFacts(sale.sale_id, physical.id)).toEqual({
      markers: 1,
      journal: 1,
      sales: 1,
      payments: 1,
      physical_remaining: 60,
    });
    expect(await (await api.get(`batches/${batch.id}`)).json()).toMatchObject({
      qty_remaining: "3.000",
    });
    expect(submissions).toEqual([result.operation_id]);
    await expect(
      dialog.getByRole("button", { name: "Открыть чек возврата", exact: true }),
    ).toHaveCount(0);
    await dialog.getByRole("button", { name: "Готово", exact: true }).click();
    await expect(dialog).not.toBeVisible();
    expect(recoveryWrites).toEqual([]);
    expect(
      await page.evaluate(() =>
        Object.keys(localStorage).filter((key) => key.startsWith("sales:mixedRefund:v1:")),
      ),
    ).toEqual([]);
    await verifyMixedQuarantineQueue(
      page,
      api,
      sale,
      result.operation_id,
      legacy.brand_name,
      physical.brand_name,
      "0.001",
      true,
    );
    expect(mixedZeroFacts(sale.sale_id, physical.id)).toEqual({
      markers: 1,
      journal: 1,
      sales: 1,
      payments: 1,
      physical_remaining: 60,
    });
  } finally {
    await api.dispose();
    await anonymous.dispose();
  }
});
