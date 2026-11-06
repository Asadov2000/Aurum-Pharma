import { request, test, type Page } from "@playwright/test";

import { createScopedRehearsalCashier } from "./cashierRehearsalAccess";
import {
  apiContext,
  apiLogin,
  catalogSearchKey,
  clearLoginRateLimit,
  expect,
  loginInBrowser,
  OWNER,
} from "./helpers";
import {
  readRehearsalLot,
  rehearsalFacts,
  seedRehearsal,
  verifyPendingQuarantine,
  verifyRehearsalReports,
  verifySavedOperations,
  type RehearsalRefund,
  type RehearsalSale,
} from "./onlineCashRehearsalFixture";

test.use({ trace: "off", screenshot: "off", video: "off" });

async function addFormat(
  page: Page,
  medicineName: string,
  format: "unit" | "blister" | "pack",
  quantity: number,
) {
  const editor = page.getByRole("region", { name: "Продажа упаковками, блистерами и поштучно" });
  if (!(await editor.count())) {
    await page.getByRole("button", { name: "Упаковки" }).click();
  }
  await expect(editor).toBeVisible();
  await editor.getByLabel("Название лекарства").fill(catalogSearchKey(medicineName));
  await editor.getByRole("option", { name: new RegExp(medicineName) }).click();
  const formats = {
    unit: ["Поштучно", "Количество, шт."],
    blister: ["Блистер", "Количество блистеров"],
    pack: ["Упаковка", "Количество упаковок"],
  } as const;
  const [label, field] = formats[format];
  await editor.getByRole("radio", { name: label, exact: true }).click();
  await editor.getByLabel(field, { exact: true }).fill(String(quantity));
  const saved = page.waitForResponse(
    (response) =>
      response.request().method() === "PUT" &&
      /\/sales\/[^/]+\/physical-cart\/items\/[^/]+$/.test(new URL(response.url()).pathname),
  );
  await editor.getByRole("button", { name: "Добавить в корзину", exact: true }).click();
  const response = await saved;
  expect(response.ok()).toBe(true);
  await expect(editor.getByRole("listitem")).toHaveCount(1);
  return (await response.json()) as { sale_id: string; id: string };
}

async function sellFormat(
  page: Page,
  medicineName: string,
  format: "unit" | "blister" | "pack",
  quantity: number,
  total: string,
) {
  const draft = await addFormat(page, medicineName, format, quantity);
  const payment = page.getByRole("region", { name: "Оплата наличными", exact: true });
  await expect(payment.getByText(`${total} TJS`, { exact: true }).first()).toBeVisible();
  await payment.getByLabel("Получено наличными, TJS").fill(total);
  const finished = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname === `/api/v1/sales/${draft.sale_id}/physical-cash/complete`,
  );
  const checked = page.waitForResponse(
    (response) =>
      response.request().method() === "GET" &&
      new URL(response.url()).pathname ===
        `/api/v1/sales/${draft.sale_id}/physical-cart/dispensing-preflight`,
  );
  await payment.getByRole("button", { name: "Завершить продажу", exact: true }).click();
  const preflight = await checked;
  expect(preflight.ok()).toBe(true);
  const authority = (await preflight.json()) as { sale_id: string; requires_prescription: boolean };
  expect(authority.sale_id === draft.sale_id && authority.requires_prescription === false).toBe(
    true,
  );
  expect(Object.keys(authority).sort()).toEqual(["requires_prescription", "sale_id"]);
  const response = await finished;
  expect(response.ok()).toBe(true);
  const sale = (await response.json()) as RehearsalSale;
  expect(sale.sale_id === draft.sale_id).toBe(true);
  expect(sale.total_amount).toBe(total);
  expect(sale.lines).toMatchObject([
    {
      sale_format: format,
      quantity,
      base_units:
        format === "unit" ? quantity : format === "blister" ? quantity * 10 : quantity * 30,
    },
  ]);
  await expect(
    page.getByText(`Чек № ${sale.receipt_number} оформлен`, { exact: true }),
  ).toBeVisible();
  const savedRows = page.getByRole("list", { name: "Товары завершённого чека", exact: true });
  await expect(savedRows.getByRole("listitem")).toHaveCount(1);
  await expect(savedRows.getByText(medicineName, { exact: true })).toBeVisible();
  await expect(savedRows.getByText(`${total} TJS`, { exact: true })).toBeVisible();
  await expect(savedRows.getByRole("button")).toHaveCount(0);
  await expect(page.getByText("Чек пуст", { exact: true })).toHaveCount(0);
  return sale;
}

async function refundOneUnit(page: Page, sale: RehearsalSale, medicineName: string) {
  await page.goto("/sales");
  const filtered = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return (
      url.pathname === "/api/v1/sales" &&
      url.searchParams.get("receipt_number") === sale.receipt_number
    );
  });
  await page.getByLabel("№ чека").fill(sale.receipt_number);
  const filteredResponse = await filtered;
  expect(filteredResponse.ok()).toBe(true);
  const listed = (await filteredResponse.json()) as { items: { id: string }[] };
  const saleIndex = listed.items.findIndex((item) => item.id === sale.sale_id);
  expect(saleIndex).toBeGreaterThanOrEqual(0);
  // Receipt counters belong to registers; earlier tests can have the same number.
  await page
    .getByRole("button", { name: `Открыть чек № ${sale.receipt_number}`, exact: true })
    .nth(saleIndex)
    .click();
  await page
    .getByRole("dialog", { name: `Чек № ${sale.receipt_number}`, exact: true })
    .getByRole("button", { name: "Оформить возврат", exact: true })
    .click();
  const dialog = page.getByRole("dialog", { name: "Возврат лекарства", exact: true });
  await dialog.getByRole("checkbox", { name: medicineName, exact: true }).check();
  await dialog.getByLabel("Что возвращают", { exact: true }).selectOption("unit");
  await dialog.getByLabel("Количество", { exact: true }).fill("1");
  const reason = dialog.getByLabel(/^Причина возврата/);
  if (await reason.count()) {
    await reason.selectOption("customer_cancelled");
    await dialog.getByLabel(/^Пояснение/).fill("Вымышленная учебная репетиция");
  }
  const quoted = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname ===
        `/api/v1/sales/${sale.sale_id}/physical-cash-refunds/quote`,
  );
  await dialog.getByRole("button", { name: "Рассчитать возврат", exact: true }).click();
  const quote = await quoted;
  expect(quote.ok()).toBe(true);
  expect(await quote.json()).toMatchObject({ total_amount: "1.00", lines: [{ base_units: 1 }] });
  const completed = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname ===
        `/api/v1/sales/${sale.sale_id}/physical-cash-refunds/complete`,
  );
  await dialog.getByRole("button", { name: "Оформить возврат наличными", exact: true }).click();
  const response = await completed;
  expect(response.ok()).toBe(true);
  const refund = (await response.json()) as RehearsalRefund;
  expect(refund).toMatchObject({ state: "completed", total_amount: "1.00" });
  expect(refund.parent_sale_id === sale.sale_id && refund.sale_id !== sale.sale_id).toBe(true);
  await expect(
    dialog.getByRole("button", { name: "Открыть чек возврата", exact: true }),
  ).toBeVisible();
  return refund;
}

async function openSavedReceipts(
  page: Page,
  medicineName: string,
  sales: RehearsalSale[],
  refund: RehearsalRefund,
) {
  const receipts = [
    ...sales.map((sale) => ({
      id: sale.sale_id,
      number: sale.receipt_number,
      total: sale.total_amount,
    })),
    { id: refund.sale_id, number: refund.receipt.receipt_number, total: refund.total_amount },
  ];
  await page.goto("/sales");
  for (const receipt of receipts) {
    const filtered = page.waitForResponse((response) => {
      const url = new URL(response.url());
      return (
        url.pathname === "/api/v1/sales" &&
        url.searchParams.get("receipt_number") === receipt.number
      );
    });
    await page.getByLabel("№ чека").fill(receipt.number);
    const response = await filtered;
    expect(response.ok()).toBe(true);
    const listed = (await response.json()) as { items: { id: string }[] };
    const index = listed.items.findIndex((item) => item.id === receipt.id);
    expect(index).toBeGreaterThanOrEqual(0);
    await page
      .getByRole("button", { name: `Открыть чек № ${receipt.number}`, exact: true })
      .nth(index)
      .click();
    const dialog = page.getByRole("dialog", { name: `Чек № ${receipt.number}`, exact: true });
    await expect(dialog.getByText(medicineName, { exact: true })).toBeVisible();
    const total = Number(receipt.total).toLocaleString("ru-RU", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
    await expect(dialog.getByText(`${total} TJS`, { exact: true }).first()).toBeVisible();
    await dialog
      .getByRole("button", { name: "Закрыть", exact: true })
      .filter({ hasText: /^Закрыть$/ })
      .click();
  }
}

test("branch-scoped cashier conserves 60 units and closes three sales, one refund and one write-off at 142 TJS", async ({
  page,
  browser,
}) => {
  test.setTimeout(180_000);
  clearLoginRateLimit(OWNER.email);
  const anonymous = await request.newContext();
  const tokens = await apiLogin(anonymous, OWNER);
  const api = await apiContext(tokens.access_token);
  let cashier: Awaited<ReturnType<typeof createScopedRehearsalCashier>> | undefined;
  const warehouse = await browser.newPage({
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:15173",
  });
  try {
    const fixture = await seedRehearsal(api);
    cashier = await createScopedRehearsalCashier(api, fixture);
    const financialPosts: string[] = [];
    const catalogReads: string[] = [];
    page.on("request", (outgoing) => {
      const pathname = new URL(outgoing.url()).pathname;
      if (
        outgoing.method() === "POST" &&
        /\/physical-cash(?:-refunds)?\/(complete|cancel)$/.test(pathname)
      ) {
        financialPosts.push(pathname);
      }
      if (outgoing.method() === "GET" && /^\/api\/v1\/catalog\/[0-9a-f-]+$/i.test(pathname))
        catalogReads.push(pathname);
    });
    await page.setViewportSize({ width: 1366, height: 900 });
    await loginInBrowser(page, cashier.credentials);
    await page.goto("/pos");
    await expect(page.getByLabel(/^Касса$/)).toBeDisabled();
    await expect(page.getByLabel(/^Касса$/)).toHaveValue(fixture.register.name);
    await expect(page.getByText(cashier.hiddenRegister.name, { exact: true })).toHaveCount(0);
    await page.getByLabel("Наличные в кассе на начало смены").fill("100");
    await page.getByRole("button", { name: "Открыть смену", exact: true }).click();
    await expect(page.getByText("Смена открыта")).toBeVisible();
    const sales: RehearsalSale[] = [];
    for (const [format, quantity, amount, remaining] of [
      ["unit", 3, "3.00", "57"],
      ["blister", 1, "10.00", "47"],
      ["pack", 1, "30.00", "17"],
    ] as const) {
      const sale = await sellFormat(page, fixture.medicine.brand_name, format, quantity, amount);
      sales.push(sale);
      expect((await readRehearsalLot(api, fixture)).base_units).toBe(remaining);
      await page.getByRole("button", { name: "+ Новая продажа", exact: true }).click();
    }
    expect(new Set(sales.map((sale) => sale.sale_id)).size).toBe(3);
    const sold = rehearsalFacts(fixture);
    expect(sold).toMatchObject({
      sales: 3,
      returns: 0,
      payments: 3,
      checkouts: 3,
      remaining: 17,
      quarantine: 0,
      net: "43.00",
    });
    const firstSale = sales[0];
    if (!firstSale) throw new Error("The first rehearsal sale is missing");
    const refund = await refundOneUnit(page, firstSale, fixture.medicine.brand_name);
    expect(rehearsalFacts(fixture)).toMatchObject({
      sales: 3,
      returns: 1,
      payments: 4,
      remaining: 17,
      quarantine: 1,
      net: "42.00",
      completedDigest: sold.completedDigest,
    });
    const stock = await api.get(`batches/physical/${fixture.lotId}/write-off-stock`, {
      params: { branch_id: fixture.branch.id, page: 1, page_size: 50 },
    });
    expect(stock.ok()).toBe(true);
    const opened = (await stock.json()) as {
      opened_packs: {
        pack_id: string;
        state: { opened_blisters: { number: number; remaining_units: number }[] };
      }[];
    };
    const pack = opened.opened_packs.find((candidate) =>
      candidate.state.opened_blisters.some((blister) => blister.remaining_units >= 2),
    );
    const blister = pack?.state.opened_blisters.find((candidate) => candidate.remaining_units >= 2);
    if (!pack || !blister)
      throw new Error("The sale did not preserve its opened blister remainder");
    await loginInBrowser(warehouse, OWNER);
    await warehouse.goto("/batches");
    await warehouse.getByRole("button", { name: "Поштучные партии", exact: true }).click();
    const inventory = warehouse.getByRole("region", { name: "Поштучные партии", exact: true });
    await inventory.getByLabel("Аптека", { exact: true }).selectOption(fixture.branch.id);
    await inventory.getByRole("button", { name: "Показать партии", exact: true }).click();
    await expect(inventory.getByText("17 табл.", { exact: true })).toBeVisible();
    await inventory.getByRole("button", { name: "Списать", exact: true }).click();
    const writeoff = warehouse.getByRole("dialog", {
      name: "Списание поштучного остатка",
      exact: true,
    });
    await writeoff.getByLabel("Причина", { exact: true }).selectOption("damaged");
    await writeoff.getByLabel("Закрытые упаковки", { exact: true }).fill("0");
    await writeoff
      .getByLabel(`Блистер ${blister.number} · ${pack.pack_id}`, { exact: true })
      .fill("2");
    await writeoff.getByRole("button", { name: "Проверить списание", exact: true }).click();
    const written = warehouse.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        new URL(response.url()).pathname === "/api/v1/batches/physical/write-offs",
    );
    await warehouse.getByRole("button", { name: "Подтвердить списание", exact: true }).click();
    expect((await written).ok()).toBe(true);
    await expect(warehouse.getByText("Списание выполнено.", { exact: true })).toBeVisible();
    await expect(inventory.getByText("15 табл.", { exact: true })).toBeVisible();
    const afterWriteoff = rehearsalFacts(fixture);
    expect(afterWriteoff).toMatchObject({
      sales: 3,
      returns: 1,
      payments: 4,
      remaining: 15,
      quarantine: 1,
      writeoffs: 1,
      writtenOff: 2,
      net: "42.00",
      completedDigest: sold.completedDigest,
    });
    await verifyPendingQuarantine(api, fixture, refund);
    await page.goto("/pos");
    const oversized = await addFormat(page, fixture.medicine.brand_name, "unit", 16);
    const payment = page.getByRole("region", { name: "Оплата наличными", exact: true });
    await payment.getByLabel("Получено наличными, TJS").fill("16");
    const refusal = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        new URL(response.url()).pathname ===
          `/api/v1/sales/${oversized.sale_id}/physical-cash/complete`,
    );
    await payment.getByRole("button", { name: "Завершить продажу", exact: true }).click();
    const refused = await refusal;
    expect(refused.status()).toBe(422);
    expect(await refused.json()).toMatchObject({
      error: {
        code: "business_rule_violation",
        message: "Insufficient compatible physical stock for cart",
      },
    });
    const confirmation = refused.request().postDataJSON() as { operation_id: string };
    expect(rehearsalFacts(fixture)).toEqual(afterWriteoff);
    const editor = page.getByRole("region", { name: "Продажа упаковками, блистерами и поштучно" });
    await expect(editor.getByRole("button", { name: "Удалить", exact: true })).toBeDisabled();
    await page.reload();
    const cancellation = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        new URL(response.url()).pathname ===
          `/api/v1/sales/${oversized.sale_id}/physical-cash/cancel`,
    );
    await payment.getByRole("button", { name: "Вернуться к корзине", exact: true }).click();
    const cancelled = await cancellation;
    expect(cancelled.ok()).toBe(true);
    expect(((await cancelled.json()) as { state: string }).state).toBe("cancelled");
    await expect(editor.getByRole("button", { name: "Удалить", exact: true })).toBeEnabled();
    const terminal = await cashier.api.get(
      `sales/${oversized.sale_id}/physical-cash/operations/${confirmation.operation_id}`,
    );
    expect(terminal.ok()).toBe(true);
    expect(((await terminal.json()) as { state: string }).state).toBe("cancelled");
    expect(
      (
        await cashier.api.post(`sales/${oversized.sale_id}/physical-cash/complete`, {
          data: confirmation,
        })
      ).status(),
    ).toBe(409);
    const removed = page.waitForResponse(
      (response) =>
        response.request().method() === "DELETE" &&
        new URL(response.url()).pathname ===
          `/api/v1/sales/${oversized.sale_id}/physical-cart/items/${oversized.id}`,
    );
    await editor.getByRole("button", { name: "Удалить", exact: true }).click();
    expect((await removed).ok()).toBe(true);
    await expect(editor.getByRole("listitem")).toHaveCount(0);
    if (await editor.count()) {
      await page.getByRole("button", { name: "Вернуться к обычной продаже", exact: true }).click();
    }
    await page.reload();
    await verifySavedOperations(cashier.api, sales, refund);
    await verifyPendingQuarantine(api, fixture, refund);
    expect(rehearsalFacts(fixture)).toEqual(afterWriteoff);
    await page.getByRole("button", { name: "Закрыть смену", exact: true }).click();
    const closing = page.getByRole("dialog");
    await closing.getByLabel("Наличные после пересчёта").fill("142");
    const closed = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        new URL(response.url()).pathname === `/api/v1/shifts/${firstSale.shift_id}/close`,
    );
    await closing.getByRole("button", { name: "Подтвердить закрытие смены", exact: true }).click();
    const closedResponse = await closed;
    expect(closedResponse.ok()).toBe(true);
    expect(await closedResponse.json()).toMatchObject({
      status: "closed",
      closing_difference: "0.00",
    });
    await expect(page.getByLabel("Наличные в кассе на начало смены")).toBeVisible();
    await verifyRehearsalReports(api, fixture, sales, refund);
    await verifySavedOperations(cashier.api, sales, refund);
    await verifyPendingQuarantine(api, fixture, refund);
    const postsBeforeHistory = financialPosts.length;
    await openSavedReceipts(page, fixture.medicine.brand_name, sales, refund);
    expect(financialPosts).toHaveLength(postsBeforeHistory);
    expect(catalogReads).toEqual([]);
    const newSale = await cashier.api.post("sales", {
      data: { operation_id: crypto.randomUUID(), register_id: fixture.register.id },
    });
    expect(newSale.status()).toBe(422);
    expect(await newSale.json()).toMatchObject({
      error: {
        code: "business_rule_violation",
        message: "No open shift for this register",
      },
    });
    expect(rehearsalFacts(fixture)).toEqual(afterWriteoff);
  } finally {
    await warehouse.close();
    await cashier?.cleanup();
    await api.dispose();
    await anonymous.dispose();
  }
});

test("an open cashier tab drops the register after branch access is revoked", async ({ page }) => {
  test.setTimeout(90_000);
  clearLoginRateLimit(OWNER.email);
  const anonymous = await request.newContext();
  const tokens = await apiLogin(anonymous, OWNER);
  const owner = await apiContext(tokens.access_token);
  let cashier: Awaited<ReturnType<typeof createScopedRehearsalCashier>> | undefined;
  try {
    const fixture = await seedRehearsal(owner);
    cashier = await createScopedRehearsalCashier(owner, fixture);
    await page.clock.install();
    await loginInBrowser(page, cashier.credentials);
    await page.goto("/pos");
    const register = page.getByLabel(/^Касса$/);
    const openShift = page.getByRole("button", { name: "Открыть смену", exact: true });
    await expect(register).toHaveValue(fixture.register.name);
    await expect(openShift).toBeEnabled();

    await cashier.revokeAccess();
    await page.bringToFront();
    // Advance the foreground tab's scheduled identity refresh without reloading it.
    await page.clock.fastForward(61_000);
    await expect(
      page.getByText(/^Этот раздел не входит в доступы вашего аккаунта\./),
    ).toBeVisible();
    await expect(page).toHaveURL(/\/pos$/);
    await expect(openShift).toHaveCount(0);
    await expect(page.getByText(fixture.register.name, { exact: true })).toHaveCount(0);
    await expect
      .poll(() =>
        page
          .locator("main input")
          .evaluateAll(
            (inputs, name) => inputs.some((input) => (input as HTMLInputElement).value === name),
            fixture.register.name,
          ),
      )
      .toBe(false);
  } finally {
    try {
      await cashier?.cleanup();
    } finally {
      await owner.dispose();
      await anonymous.dispose();
    }
  }
});

test("scoped dispensing preflight preserves Rx, owner and branch checks and fails closed before payment", async ({
  page,
}) => {
  test.setTimeout(180_000);
  const anonymous = await request.newContext();
  const tokens = await apiLogin(anonymous, OWNER);
  const ownerApi = await apiContext(tokens.access_token);
  let cashier: Awaited<ReturnType<typeof createScopedRehearsalCashier>> | undefined;
  try {
    const fixture = await seedRehearsal(ownerApi);
    expect(
      (
        await ownerApi.patch(`catalog/${fixture.medicine.id}`, {
          data: { dispensing_type: "prescription" },
        })
      ).ok(),
    ).toBe(true);
    cashier = await createScopedRehearsalCashier(ownerApi, fixture);
    await loginInBrowser(page, cashier.credentials);
    await page.goto("/pos");
    await page.getByLabel("Наличные в кассе на начало смены").fill("100");
    await page.getByRole("button", { name: "Открыть смену", exact: true }).click();
    await expect(page.getByText("Смена открыта")).toBeVisible();
    const draft = await addFormat(page, fixture.medicine.brand_name, "unit", 3);
    const path = `sales/${draft.sale_id}/physical-cart/dispensing-preflight`;
    const initial = rehearsalFacts(fixture);
    const checked = await cashier.api.get(path);
    expect(checked.ok()).toBe(true);
    expect(await checked.json()).toEqual({ sale_id: draft.sale_id, requires_prescription: true });
    // An owner remains a different cashier; global permissions do not authorize
    // reading another actor's draft through this narrow POS endpoint.
    expect((await ownerApi.get(path)).status()).toBe(403);
    expect(
      (
        await cashier.api.get(`sales/${crypto.randomUUID()}/physical-cart/dispensing-preflight`)
      ).status(),
    ).toBe(404);
    expect(
      (
        await ownerApi.post("shifts/open", {
          data: { register_id: cashier.hiddenRegister.id, opening_cash: "0.00" },
        })
      ).ok(),
    ).toBe(true);
    const foreignCreated = await ownerApi.post("sales", {
      data: { register_id: cashier.hiddenRegister.id, operation_id: crypto.randomUUID() },
    });
    expect(foreignCreated.ok()).toBe(true);
    const foreign = (await foreignCreated.json()) as { id: string };
    expect(
      (await cashier.api.get(`sales/${foreign.id}/physical-cart/dispensing-preflight`)).status(),
    ).toBe(403);
    expect(rehearsalFacts(fixture)).toEqual(initial);

    const financialPosts: string[] = [];
    const catalogReads: string[] = [];
    page.on("request", (outgoing) => {
      const pathname = new URL(outgoing.url()).pathname;
      if (outgoing.method() === "POST" && /\/physical-cash\/(intent|complete)$/.test(pathname))
        financialPosts.push(pathname);
      if (outgoing.method() === "GET" && /^\/api\/v1\/catalog\/[0-9a-f-]+$/i.test(pathname))
        catalogReads.push(pathname);
    });
    const payment = page.getByRole("region", { name: "Оплата наличными", exact: true });
    const preflightPattern = `**/api/v1/${path}`;
    // Deliberate transport failure, never a mocked success or a bypass of Rx.
    await page.route(preflightPattern, (route) => route.abort("failed"));
    await payment.getByLabel("Получено наличными, TJS").fill("3");
    const failedRead = page.waitForEvent("requestfailed", {
      predicate: (outgoing) => new URL(outgoing.url()).pathname === `/api/v1/${path}`,
    });
    await payment.getByRole("button", { name: "Завершить продажу", exact: true }).click();
    await failedRead;
    await expect(payment.getByRole("alert")).toBeVisible();
    await expect(
      payment.getByRole("button", { name: "Завершить продажу", exact: true }),
    ).toBeEnabled();
    expect(financialPosts).toEqual([]);
    expect(rehearsalFacts(fixture)).toEqual(initial);
    await page.unroute(preflightPattern);
    await page.reload();
    await payment.getByLabel("Получено наличными, TJS").fill("3");
    await payment.getByRole("button", { name: "Завершить продажу", exact: true }).click();
    const prescription = page.getByRole("dialog", { name: "Данные рецепта" });
    await expect(prescription).toBeVisible();
    expect(financialPosts).toEqual([]);
    expect(rehearsalFacts(fixture)).toEqual(initial);
    await prescription.getByLabel("Номер рецепта").fill("E2E-SYNTHETIC-RX");
    await prescription.getByRole("button", { name: "Сохранить", exact: true }).click();
    await expect(prescription).toBeHidden();
    const completed = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        new URL(response.url()).pathname ===
          `/api/v1/sales/${draft.sale_id}/physical-cash/complete`,
    );
    await payment.getByRole("button", { name: "Завершить продажу", exact: true }).click();
    const result = await completed;
    expect(result.ok()).toBe(true);
    const sale = (await result.json()) as RehearsalSale;
    expect(sale.sale_id === draft.sale_id && sale.total_amount === "3.00").toBe(true);
    await expect(
      page.getByText(`Чек № ${sale.receipt_number} оформлен`, { exact: true }),
    ).toBeVisible();
    expect(rehearsalFacts(fixture)).toMatchObject({
      sales: 1,
      payments: 1,
      checkouts: 1,
      remaining: 57,
      net: "3.00",
    });
    expect((await cashier.api.get(path)).status()).toBe(422);
    expect(catalogReads).toEqual([]);
  } finally {
    await cashier?.cleanup();
    await ownerApi.dispose();
    await anonymous.dispose();
  }
});
