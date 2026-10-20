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
  const supplier = await seedSupplier(api, uniqueName("Physical cash supplier"));
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

function persistedFacts(result: CheckoutResult, catalogId: string): string {
  // This helper permits only SELECT against the launcher's disposable E2E database.
  for (const value of [result.sale_id, result.stock_command_id, catalogId]) {
    expect(value).toMatch(/^[0-9a-f-]{36}$/i);
  }
  const sql = `SELECT json_build_object(
    'checkouts', (SELECT count(*) FROM pos_physical_checkout WHERE sale_id='${result.sale_id}'),
    'payments', (SELECT count(*) FROM sale_payment WHERE sale_id='${result.sale_id}'),
    'commands', (SELECT count(*) FROM unit_stock_cart_command_v2 WHERE id='${result.stock_command_id}'),
    'remaining', (SELECT sum(l.closed_packs * 20 + COALESCE(
      (SELECT sum(p.remaining_units) FROM unit_stock_opened_pack_v2 p WHERE p.lot_id=l.id), 0))
      FROM unit_stock_lot_v2 l WHERE l.catalog_id='${catalogId}')
  );`;
  return dockerExec(E2E_POSTGRES_CONTAINER, [
    "psql",
    "-U",
    "postgres",
    "-d",
    E2E_POSTGRES_DB,
    "-At",
    "-c",
    sql,
  ]).trim();
}

for (const { loseResponse, requiresPrescription } of [
  { loseResponse: false, requiresPrescription: false },
  { loseResponse: true, requiresPrescription: false },
  { loseResponse: false, requiresPrescription: true },
]) {
  test(
    loseResponse
      ? "recovers physical cash after a lost completion response, reload and closed shift"
      : requiresPrescription
        ? "requires a prescription before completing a physical cash sale"
        : "completes unit, blister and full-pack cash sale once",
    async ({ page }) => {
      test.setTimeout(120_000);
      clearLoginRateLimit(OWNER.email);
      const anonymous = await request.newContext();
      const tokens = await apiLogin(anonymous, OWNER);
      const api = await apiContext(tokens.access_token);
      try {
        const branch = await seedBranch(api, uniqueName("Physical cash branch"));
        const register = await seedRegister(api, branch.id, uniqueName("Physical cash register"));
        const medicine = await seedCatalogItem(api, uniqueName("Physical cash medicine"), "10.00");
        expect(
          (
            await api.patch(`catalog/${medicine.id}`, {
              data: {
                dispensing_type: requiresPrescription ? "prescription" : "otc",
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
        await page.getByRole("button", { name: "Открыть смену" }).click();
        await expect(page.getByText("Смена открыта")).toBeVisible();
        const saleId = await addPhysicalFormats(page, medicine.brand_name);
        const panel = page.getByRole("region", { name: "Оплата наличными", exact: true });
        await expect(panel.getByText("16.00 TJS", { exact: true })).toBeVisible();
        await panel.getByLabel("Получено наличными, TJS").fill("20");
        await expect(panel.getByText("4.00 TJS", { exact: true })).toBeVisible();
        if (requiresPrescription) {
          const paymentRequests: string[] = [];
          page.on("request", (outgoing) => {
            const pathname = new URL(outgoing.url()).pathname;
            if (outgoing.method() === "POST" && pathname.includes("/physical-cash/")) {
              paymentRequests.push(pathname);
            }
          });
          await panel.getByRole("button", { name: "Завершить продажу", exact: true }).click();
          const prescription = page.getByRole("dialog", { name: "Данные рецепта" });
          await expect(prescription).toBeVisible();
          await expect(
            panel.getByText("Сначала заполните рецепт. Оплата ещё не начата."),
          ).toBeVisible();
          expect(paymentRequests).toEqual([]);
          await prescription.getByLabel("Номер рецепта").fill("E2E-SYNTHETIC-RX");
          await prescription.getByRole("button", { name: "Сохранить", exact: true }).click();
          await expect(prescription).toBeHidden();
        }
        let completed: CheckoutResult | null = null;
        const submitted: string[] = [];
        let blockStatus = false;
        await page.route("**/api/v1/sales/*/physical-cash/operations/*", async (route) => {
          if (blockStatus) await route.abort("failed");
          else await route.continue();
        });
        await page.route("**/api/v1/sales/*/physical-cash/complete", async (route) => {
          submitted.push((route.request().postDataJSON() as { operation_id: string }).operation_id);
          const response = await route.fetch();
          expect(response.ok()).toBe(true);
          completed = (await response.json()) as CheckoutResult;
          if (loseResponse) {
            blockStatus = true;
            await route.abort("failed");
          } else await route.fulfill({ response });
        });
        await panel.getByRole("button", { name: "Завершить продажу", exact: true }).click();
        await expect.poll(() => completed !== null).toBe(true);
        const result = completed as CheckoutResult | null;
        if (!result) throw new Error("Cash completion response was not observed");
        expect(result).toMatchObject({
          schema_version: 3,
          sale_id: saleId,
          total_amount: "16.00",
          lines: [
            { sale_format: "unit", quantity: 2, base_units: 2 },
            { sale_format: "blister", quantity: 1, base_units: 10 },
            { sale_format: "pack", quantity: 1, base_units: 20 },
          ],
          payments: [{ id: result.operation_id, method: "cash", amount: "16.00" }],
        });
        if (loseResponse) {
          await expect(
            panel.getByRole("button", { name: "Проверить и завершить оплату" }),
          ).toBeVisible();
          expect(
            (
              await api.post(`shifts/${result.shift_id}/close`, {
                data: { closing_cash_actual: "116.00" },
              })
            ).ok(),
          ).toBe(true);
          blockStatus = false;
          await page.reload();
        }
        await expect(
          page.getByText(`Чек № ${result.receipt_number} оформлен`, { exact: true }),
        ).toBeVisible();
        await expect(page.getByRole("button", { name: "Печать чека", exact: true })).toBeVisible();
        expect(submitted).toEqual([result.operation_id]);
        const recovered = await api.get(
          `sales/${saleId}/physical-cash/operations/${result.operation_id}`,
        );
        expect(recovered.ok()).toBe(true);
        expect(await recovered.json()).toMatchObject({ state: "completed", result });
        expect(JSON.parse(persistedFacts(result, medicine.id))).toEqual({
          checkouts: 1,
          payments: 1,
          commands: 1,
          remaining: 48,
        });
        const repeated = await api.get(
          `sales/${saleId}/physical-cash/operations/${result.operation_id}`,
        );
        expect(await repeated.json()).toMatchObject({ state: "completed", result });
      } finally {
        await api.dispose();
        await anonymous.dispose();
      }
    },
  );
}

function preparationFacts(saleId: string, catalogId: string): string {
  for (const value of [saleId, catalogId]) expect(value).toMatch(/^[0-9a-f-]{36}$/i);
  const sql = `SELECT jsonb_build_object(
    'sale', (SELECT jsonb_build_object('status',status,'total',total_amount,
      'receipt',receipt_number,'completed_at',completed_at) FROM sale WHERE id='${saleId}'),
    'payments', (SELECT count(*) FROM sale_payment WHERE sale_id='${saleId}'),
    'attempts', (SELECT count(*) FROM pos_payment_attempt WHERE sale_id='${saleId}'),
    'checkouts', (SELECT count(*) FROM pos_physical_checkout WHERE sale_id='${saleId}'),
    'commands', (SELECT count(*) FROM unit_stock_cart_command_v2
      WHERE checkout_sale_id='${saleId}'),
    'stock', (SELECT jsonb_agg(to_jsonb(l) ORDER BY l.id)
      FROM unit_stock_lot_v2 l WHERE catalog_id='${catalogId}'),
    'opened', (SELECT jsonb_agg(to_jsonb(p) ORDER BY p.id)
      FROM unit_stock_opened_pack_v2 p WHERE p.lot_id IN
        (SELECT id FROM unit_stock_lot_v2 WHERE catalog_id='${catalogId}')),
    'movements', (SELECT jsonb_agg(to_jsonb(m) ORDER BY m.id)
      FROM unit_stock_movement_v2 m WHERE m.lot_id IN
        (SELECT id FROM unit_stock_lot_v2 WHERE catalog_id='${catalogId}'))
  );`;
  return dockerExec(E2E_POSTGRES_CONTAINER, [
    "psql",
    "-U",
    "postgres",
    "-d",
    E2E_POSTGRES_DB,
    "-At",
    "-c",
    sql,
  ]).trim();
}

test("recovers card preparation after a lost binding response and cancels without payment", async ({
  page,
}) => {
  test.setTimeout(120_000);
  clearLoginRateLimit(OWNER.email);
  const anonymous = await request.newContext();
  const tokens = await apiLogin(anonymous, OWNER);
  const api = await apiContext(tokens.access_token);
  try {
    const branch = await seedBranch(api, uniqueName("Payment preparation branch"));
    const register = await seedRegister(api, branch.id, uniqueName("Payment preparation register"));
    const medicine = await seedCatalogItem(
      api,
      uniqueName("Payment preparation medicine"),
      "10.00",
    );
    expect(
      (
        await api.patch(`catalog/${medicine.id}`, {
          data: {
            dispensing_type: "otc",
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
    await page.getByRole("button", { name: "Открыть смену" }).click();
    await expect(page.getByText("Смена открыта")).toBeVisible();
    const saleId = await addPhysicalFormats(page, medicine.brand_name);
    const before = preparationFacts(saleId, medicine.id);
    const preparation = page.getByRole("region", {
      name: "Подготовка электронной оплаты",
      exact: true,
    });
    const editor = page.getByRole("region", { name: "Продажа упаковками, блистерами и поштучно" });
    const cash = page.getByRole("region", { name: "Оплата наличными", exact: true });
    await preparation.getByRole("button", { name: "Подготовка карты/QR", exact: true }).click();
    await expect(preparation.getByLabel("Картой, TJS", { exact: true })).toHaveValue("16.00");
    await preparation.getByRole("button", { name: "Подготовить оплату", exact: true }).click();
    await expect(
      preparation.getByRole("button", { name: "Закрепить план", exact: true }),
    ).toBeEnabled();
    let boundId = "";
    const bindingPath = `**/api/v1/sales/${saleId}/unit-payment-plan/intents/*/bind`;
    await page.route(bindingPath, async (route) => {
      const response = await route.fetch();
      expect(response.ok()).toBe(true);
      const bound = (await response.json()) as { state: string; operation_id: string };
      expect(bound.state).toBe("bound");
      boundId = bound.operation_id;
      await route.abort("failed");
    });
    await preparation.getByRole("button", { name: "Закрепить план", exact: true }).click();
    await expect.poll(() => boundId).toMatch(/^[0-9a-f-]{36}$/i);
    await expect(
      editor.getByRole("button", { name: "Изменить", exact: true }).first(),
    ).toBeDisabled();
    await expect(
      cash.getByRole("button", { name: "Завершить продажу", exact: true }),
    ).toBeDisabled();
    expect(preparationFacts(saleId, medicine.id)).toBe(before);
    await page.unroute(bindingPath);
    const recovered = page.waitForResponse(
      (response) =>
        response.request().method() === "GET" &&
        new URL(response.url()).pathname ===
          `/api/v1/sales/${saleId}/unit-payment-plan/active-binding` &&
        response.ok(),
    );
    await page.reload();
    expect(await (await recovered).json()).toMatchObject({
      sale_id: saleId,
      intent: {
        operation_id: boundId,
        plan: { request: { payments: [{ method: "card", amount: "16.00" }] } },
      },
      binding: { operation_id: boundId, state: "bound" },
    });
    await expect(
      preparation.getByText("План закреплён. Корзина защищена от изменений.", { exact: true }),
    ).toBeVisible();
    await expect(
      editor.getByRole("button", { name: "Изменить", exact: true }).first(),
    ).toBeDisabled();
    await expect(
      cash.getByRole("button", { name: "Завершить продажу", exact: true }),
    ).toBeDisabled();
    const cancelled = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        new URL(response.url()).pathname ===
          `/api/v1/sales/${saleId}/unit-payment-plan/intents/${boundId}/cancel-binding`,
    );
    await preparation.getByRole("button", { name: "Отменить подготовку", exact: true }).click();
    expect((await cancelled).ok()).toBe(true);
    await expect(
      editor.getByRole("button", { name: "Изменить", exact: true }).first(),
    ).toBeEnabled();
    await expect(
      cash.getByRole("button", { name: "Завершить продажу", exact: true }),
    ).toBeEnabled();
    const late = await api.post(`sales/${saleId}/unit-payment-plan/intents/${boundId}/bind`);
    expect(late.status()).toBe(409);
    const final = await api.get(`sales/${saleId}/unit-payment-plan/active-binding`);
    expect(final.ok()).toBe(true);
    expect(await final.json()).toEqual({ sale_id: saleId, intent: null, binding: null });
    expect(preparationFacts(saleId, medicine.id)).toBe(before);
    await expect(page.getByRole("button", { name: "Печать чека", exact: true })).toHaveCount(0);
  } finally {
    await api.dispose();
    await anonymous.dispose();
  }
});

test("completes physical electronic checkout and recovers a lost completion with GET only", async ({
  page,
}) => {
  test.setTimeout(120_000);
  clearLoginRateLimit(OWNER.email);
  const anonymous = await request.newContext();
  const tokens = await apiLogin(anonymous, OWNER);
  const api = await apiContext(tokens.access_token);
  try {
    const branch = await seedBranch(api, uniqueName("Payment preparation branch"));
    const register = await seedRegister(api, branch.id, uniqueName("Payment preparation register"));
    const medicine = await seedCatalogItem(
      api,
      uniqueName("Payment preparation medicine"),
      "10.00",
    );
    expect(
      (
        await api.patch(`catalog/${medicine.id}`, {
          data: {
            dispensing_type: "otc",
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
    await page.getByRole("button", { name: "Открыть смену" }).click();
    await expect(page.getByText("Смена открыта")).toBeVisible();
    const saleId = await addPhysicalFormats(page, medicine.brand_name);

    const preparation = page.getByRole("region", {
      name: "Подготовка электронной оплаты",
      exact: true,
    });
    await preparation.getByRole("button", { name: "Подготовка карты/QR", exact: true }).click();
    await preparation.getByRole("button", { name: "Подготовить оплату", exact: true }).click();
    await preparation.getByRole("button", { name: "Закрепить план", exact: true }).click();
    const electronic = page.getByRole("region", { name: "Электронное проведение", exact: true });
    await expect(electronic).toBeVisible();
    await electronic
      .getByRole("button", { name: "Подготовить полный набор попыток", exact: true })
      .click();
    await electronic.getByRole("button", { name: "Начать явную сверку card", exact: true }).click();
    await electronic.getByLabel("Терминал", { exact: true }).fill("E2E-OPERATOR-TERMINAL");
    await electronic
      .getByLabel("Внешний номер подтверждения", { exact: true })
      .fill(uniqueName("E2E-OPERATOR-REFERENCE"));
    await electronic
      .getByRole("button", { name: "Подтвердить проверенную оплату card", exact: true })
      .click();
    const complete = electronic.getByRole("button", {
      name: "Завершить продажу по сохранённому плану",
      exact: true,
    });
    await expect(complete).toBeEnabled();
    let completed: CheckoutResult | null = null;
    const submitted: string[] = [];
    const completePath = `**/api/v1/sales/${saleId}/unit-payment-plan/intents/*/complete`;
    await page.route(completePath, async (route) => {
      submitted.push(new URL(route.request().url()).pathname);
      expect(route.request().postDataJSON()).toEqual({});
      const response = await route.fetch();
      expect(response.ok()).toBe(true);
      completed = (await response.json()) as CheckoutResult;
      await route.abort("failed");
    });
    await complete.click();
    await expect.poll(() => completed !== null).toBe(true);
    const original = completed as CheckoutResult | null;
    if (!original) throw new Error("Saved electronic result not observed");
    expect(original.payments).toHaveLength(1);
    expect(original.payments[0]).toMatchObject({ method: "card", amount: "16.00" });
    const postsAfterReload: string[] = [];
    page.on("request", (req) => {
      if (req.method() === "POST" && new URL(req.url()).pathname.includes("unit-payment-plan"))
        postsAfterReload.push(new URL(req.url()).pathname);
    });
    await page.unroute(completePath);
    await page.reload();
    await expect(
      electronic.getByText(`Сохранённый чек ${original.receipt_number}: 16.00 TJS`, {
        exact: true,
      }),
    ).toBeVisible();
    expect(postsAfterReload).toEqual([]);
    expect(submitted).toHaveLength(1);
    await electronic.getByRole("button", { name: "Принять сохранённый чек", exact: true }).click();
    await expect(page.getByRole("button", { name: "Печать чека", exact: true })).toBeVisible();
    expect(postsAfterReload).toEqual([]);
    const final = await api.get(
      `sales/${saleId}/unit-payment-plan/intents/${original.operation_id}/checkout`,
    );
    expect(final.ok()).toBe(true);
    expect(await final.json()).toEqual(original);
  } finally {
    await api.dispose();
    await anonymous.dispose();
  }
});
