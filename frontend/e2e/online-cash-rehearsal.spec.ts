import { request, test, type Page } from "@playwright/test";

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
    await page.getByRole("button", { name: "Выбрать упаковку, блистер или штуки" }).click();
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
  await payment.getByRole("button", { name: "Завершить продажу", exact: true }).click();
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

test("online cash rehearsal conserves 60 units and closes three sales, one refund and one write-off at 142 TJS", async ({
  page,
}) => {
  test.setTimeout(180_000);
  clearLoginRateLimit(OWNER.email);
  const anonymous = await request.newContext();
  const tokens = await apiLogin(anonymous, OWNER);
  const api = await apiContext(tokens.access_token);
  try {
    const fixture = await seedRehearsal(api);
    await page.setViewportSize({ width: 1366, height: 900 });
    await loginInBrowser(page, OWNER);
    await page.goto("/pos");
    await page.getByLabel(/^Касса$/).selectOption({ label: fixture.register.name });
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
    await page.goto("/batches");
    await page.getByRole("button", { name: "Поштучные партии", exact: true }).click();
    const inventory = page.getByRole("region", { name: "Поштучные партии", exact: true });
    await inventory.getByLabel("Аптека", { exact: true }).selectOption(fixture.branch.id);
    await inventory.getByRole("button", { name: "Показать партии", exact: true }).click();
    await expect(inventory.getByText("17 табл.", { exact: true })).toBeVisible();
    await inventory.getByRole("button", { name: "Списать", exact: true }).click();
    const writeoff = page.getByRole("dialog", { name: "Списание поштучного остатка", exact: true });
    await writeoff.getByLabel("Причина", { exact: true }).selectOption("damaged");
    await writeoff.getByLabel("Закрытые упаковки", { exact: true }).fill("0");
    await writeoff
      .getByLabel(`Блистер ${blister.number} · ${pack.pack_id}`, { exact: true })
      .fill("2");
    await writeoff.getByRole("button", { name: "Проверить списание", exact: true }).click();
    const written = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        new URL(response.url()).pathname === "/api/v1/batches/physical/write-offs",
    );
    await page.getByRole("button", { name: "Подтвердить списание", exact: true }).click();
    expect((await written).ok()).toBe(true);
    await expect(page.getByText("Списание выполнено.", { exact: true })).toBeVisible();
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
    await page.getByLabel(/^Касса$/).selectOption({ label: fixture.register.name });
    const oversized = await addFormat(page, fixture.medicine.brand_name, "unit", 16);
    // This negative check calls the ordinary authenticated API for a UI-created draft.
    // It proves server stock refusal, not the UI's recovery after a failed cash submission.
    const quoted = await api.get(`sales/${oversized.sale_id}/physical-cart/quote`);
    expect(quoted.ok()).toBe(true);
    const quote = (await quoted.json()) as {
      revision_hash: string;
      quote: { total_amount: string };
    };
    expect(quote.quote.total_amount).toBe("16.00");
    const confirmation = {
      operation_id: crypto.randomUUID(),
      sale_id: oversized.sale_id,
      expected_revision_hash: quote.revision_hash,
      expected_total_amount: "16.00",
      cash_tendered: "16.00",
    };
    expect(
      (
        await api.post(`sales/${oversized.sale_id}/physical-cash/intent`, { data: confirmation })
      ).ok(),
    ).toBe(true);
    const refused = await api.post(`sales/${oversized.sale_id}/physical-cash/complete`, {
      data: confirmation,
    });
    expect(refused.status()).toBe(422);
    expect(await refused.json()).toMatchObject({
      error: {
        code: "business_rule_violation",
        message: "Insufficient compatible physical stock for cart",
      },
    });
    expect(rehearsalFacts(fixture)).toEqual(afterWriteoff);
    const editor = page.getByRole("region", { name: "Продажа упаковками, блистерами и поштучно" });
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
    await verifySavedOperations(api, sales, refund);
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
    await verifySavedOperations(api, sales, refund);
    await verifyPendingQuarantine(api, fixture, refund);
    const newSale = await api.post("sales", {
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
    await api.dispose();
    await anonymous.dispose();
  }
});
