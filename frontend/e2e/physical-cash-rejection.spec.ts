import { request, test, type APIRequestContext, type Page } from "@playwright/test";

import { assertCancellationHistoryProtected } from "./cashCancellationMigrationProof";
import { dockerExec, E2E_POSTGRES_CONTAINER, E2E_POSTGRES_DB } from "./docker";
import {
  apiContext,
  apiLogin,
  selectPosProduct,
  clearLoginRateLimit,
  expect,
  loginInBrowser,
  OWNER,
} from "./helpers";
import {
  rehearsalFacts,
  seedRehearsal,
  type RehearsalFixture,
  type RehearsalSale,
} from "./onlineCashRehearsalFixture";

test.use({ trace: "off", screenshot: "off", video: "off" });

interface Confirmation {
  operation_id: string;
  sale_id: string;
  expected_revision_hash: string;
  expected_total_amount: string;
  cash_tendered: string;
}

function cancellationCount(saleId: string): number {
  expect(saleId).toMatch(/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i);
  // Existing dockerExec permits only the launcher's verified disposable database.
  return Number(
    dockerExec(E2E_POSTGRES_CONTAINER, [
      "psql",
      "-U",
      "postgres",
      "-d",
      E2E_POSTGRES_DB,
      "-At",
      "-c",
      `SELECT count(*) FROM pos_physical_cash_cancellation WHERE sale_id='${saleId}';`,
    ]).trim(),
  );
}

async function addUnits(page: Page, fixture: RehearsalFixture, quantity: number) {
  const editor = page.getByRole("region", { name: "Продажа упаковками, блистерами и поштучно" });
  await selectPosProduct(page, fixture.medicine.brand_name);
  await expect(editor).toBeVisible();
  await editor.getByRole("radio", { name: /^Штуки / }).click();
  await editor.getByLabel("Количество, шт.", { exact: true }).fill(String(quantity));
  const saved = page.waitForResponse(
    (response) =>
      response.request().method() === "PUT" &&
      /\/sales\/[^/]+\/physical-cart\/items\/[^/]+$/.test(new URL(response.url()).pathname),
  );
  await editor.getByRole("button", { name: "Добавить в чек", exact: true }).click();
  const response = await saved;
  expect(response.ok()).toBe(true);
  return { editor, item: (await response.json()) as { sale_id: string; id: string } };
}

async function prepareCash(
  api: APIRequestContext,
  fixture: RehearsalFixture,
): Promise<Confirmation> {
  expect(
    (
      await api.post("shifts/open", {
        data: { register_id: fixture.register.id, opening_cash: "100.00" },
      })
    ).ok(),
  ).toBe(true);
  const created = await api.post("sales", {
    data: { operation_id: crypto.randomUUID(), register_id: fixture.register.id },
  });
  expect(created.ok()).toBe(true);
  const sale = (await created.json()) as { id: string };
  expect(
    (
      await api.put(`sales/${sale.id}/physical-cart/items/${crypto.randomUUID()}`, {
        data: {
          catalog_id: fixture.medicine.id,
          position: 1,
          sale_format: "unit",
          quantity: 3,
          discount_amount: "0.00",
        },
      })
    ).ok(),
  ).toBe(true);
  const quoted = await api.get(`sales/${sale.id}/physical-cart/quote`);
  expect(quoted.ok()).toBe(true);
  const quote = (await quoted.json()) as { revision_hash: string; quote: { total_amount: string } };
  expect(quote.quote.total_amount).toBe("3.00");
  const confirmation = {
    operation_id: crypto.randomUUID(),
    sale_id: sale.id,
    expected_revision_hash: quote.revision_hash,
    expected_total_amount: "3.00",
    cash_tendered: "3.00",
  };
  expect(
    (await api.post(`sales/${sale.id}/physical-cash/intent`, { data: confirmation })).ok(),
  ).toBe(true);
  return confirmation;
}

test("cashier safely cancels a stock-refused cash intent, edits the cart and completes one sale", async ({
  page,
}) => {
  test.setTimeout(180_000);
  clearLoginRateLimit(OWNER.email);
  const anonymous = await request.newContext();
  const tokens = await apiLogin(anonymous, OWNER);
  const api = await apiContext(tokens.access_token);
  try {
    const fixture = await seedRehearsal(api);
    const initial = rehearsalFacts(fixture);
    await page.setViewportSize({ width: 1366, height: 900 });
    await loginInBrowser(page, OWNER);
    await page.goto("/pos");
    await page.getByLabel(/^Касса$/).selectOption({ label: fixture.register.name });
    await page.getByLabel("Наличные в кассе на начало смены").fill("100");
    await page.getByRole("button", { name: "Открыть смену", exact: true }).click();
    await expect(page.getByText("Смена открыта")).toBeVisible();
    const { editor, item } = await addUnits(page, fixture, 61);
    const cash = page.getByRole("region", { name: "Оплата наличными", exact: true });
    await cash.getByLabel("Получено наличными, TJS").fill("61");
    const rejected = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        new URL(response.url()).pathname === `/api/v1/sales/${item.sale_id}/physical-cash/complete`,
    );
    await cash.getByRole("button", { name: "Завершить продажу", exact: true }).click();
    const refusal = await rejected;
    expect(refusal.status()).toBe(422);
    expect(await refusal.json()).toMatchObject({
      error: {
        code: "business_rule_violation",
        message: "Insufficient compatible physical stock for cart",
      },
    });
    const oldRequest = refusal.request().postDataJSON() as Confirmation;
    expect(oldRequest.sale_id === item.sale_id).toBe(true);
    expect(rehearsalFacts(fixture)).toEqual(initial);
    await expect(editor.getByRole("button", { name: "Изменить", exact: true })).toBeDisabled();
    await expect(editor.getByRole("button", { name: "Убрать", exact: true })).toBeDisabled();
    const prefix = `sales/${item.sale_id}/physical-cash`;
    const statusBefore = await api.get(`${prefix}/operations/${oldRequest.operation_id}`);
    expect(statusBefore.ok()).toBe(true);
    expect(((await statusBefore.json()) as { state: string }).state).toBe("saved_draft");
    await page.reload();
    const cancel = cash.getByRole("button", { name: "Вернуться к корзине", exact: true });
    await expect(cancel).toBeEnabled();
    await expect(editor.getByRole("button", { name: "Изменить", exact: true })).toBeDisabled();
    const cancelled = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        new URL(response.url()).pathname === `/api/v1/${prefix}/cancel`,
    );
    await cancel.click();
    const cancellation = await cancelled;
    expect(cancellation.ok()).toBe(true);
    expect(((await cancellation.json()) as { state: string }).state).toBe("cancelled");
    await expect(editor.getByRole("button", { name: "Изменить", exact: true })).toBeEnabled();
    expect(rehearsalFacts(fixture)).toEqual(initial);
    expect(cancellationCount(item.sale_id)).toBe(1);
    const terminal = await api.get(`${prefix}/operations/${oldRequest.operation_id}`);
    expect(terminal.ok()).toBe(true);
    expect(((await terminal.json()) as { state: string }).state).toBe("cancelled");

    await editor.getByRole("button", { name: "Изменить", exact: true }).click();
    await editor.getByLabel("Количество, шт.", { exact: true }).fill("3");
    const saved = page.waitForResponse(
      (response) =>
        response.request().method() === "PUT" &&
        new URL(response.url()).pathname ===
          `/api/v1/sales/${item.sale_id}/physical-cart/items/${item.id}`,
    );
    await editor.getByRole("button", { name: "Сохранить строку", exact: true }).click();
    expect((await saved).ok()).toBe(true);
    await cash.getByLabel("Получено наличными, TJS").fill("3");
    const completed = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        new URL(response.url()).pathname === `/api/v1/${prefix}/complete`,
    );
    await cash.getByRole("button", { name: "Завершить продажу", exact: true }).click();
    const payment = await completed;
    expect(payment.ok()).toBe(true);
    const sale = (await payment.json()) as RehearsalSale;
    expect(sale.total_amount).toBe("3.00");
    expect(sale.operation_id === oldRequest.operation_id).toBe(false);
    const paid = rehearsalFacts(fixture);
    expect(paid).toMatchObject({
      sales: 1,
      returns: 0,
      payments: 1,
      checkouts: 1,
      remaining: 57,
      net: "3.00",
    });
    const late = await api.post(`${prefix}/complete`, { data: oldRequest });
    expect(late.status()).toBe(409);
    expect(rehearsalFacts(fixture)).toEqual(paid);
    await expect(
      page.getByText(`Чек № ${sale.receipt_number} оформлен`, { exact: true }),
    ).toBeVisible();
    await page.getByRole("button", { name: "+ Новая продажа", exact: true }).click();
    await page.getByRole("button", { name: "Закрыть смену", exact: true }).click();
    const closing = page.getByRole("dialog");
    await closing.getByLabel("Наличные после пересчёта").fill("103");
    const closed = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        new URL(response.url()).pathname === `/api/v1/shifts/${sale.shift_id}/close`,
    );
    await closing.getByRole("button", { name: "Подтвердить закрытие смены", exact: true }).click();
    expect((await closed).ok()).toBe(true);
    const zReport = await api.get(`shifts/${sale.shift_id}/z-report`);
    expect(zReport.ok()).toBe(true);
    expect(await zReport.json()).toMatchObject({
      closing_cash_expected: "103.00",
      closing_difference: "0.00",
      sales_count: 1,
    });
    expect(rehearsalFacts(fixture)).toEqual(paid);
    assertCancellationHistoryProtected();
  } finally {
    await api.dispose();
    await anonymous.dispose();
  }
});

test("concurrent physical cash completion and cancellation commit exactly one terminal outcome", async () => {
  test.setTimeout(120_000);
  clearLoginRateLimit(OWNER.email);
  const anonymous = await request.newContext();
  const tokens = await apiLogin(anonymous, OWNER);
  const api = await apiContext(tokens.access_token);
  try {
    const fixture = await seedRehearsal(api);
    const initial = rehearsalFacts(fixture);
    const confirmation = await prepareCash(api, fixture);
    const prefix = `sales/${confirmation.sale_id}/physical-cash`;
    const outcomes = await Promise.allSettled([
      api.post(`${prefix}/complete`, { data: confirmation }),
      api.post(`${prefix}/cancel`, { data: confirmation }),
    ]);
    const completion = outcomes[0];
    const cancellation = outcomes[1];
    if (completion?.status !== "fulfilled" || cancellation?.status !== "fulfilled")
      throw new Error("Concurrent cash requests did not return authoritative HTTP outcomes");
    expect(cancellation.value.ok()).toBe(true);
    const cancellationBody = (await cancellation.value.json()) as {
      state: string;
      result?: unknown;
    };
    const terminalResponse = await api.get(`${prefix}/operations/${confirmation.operation_id}`);
    expect(terminalResponse.ok()).toBe(true);
    const terminal = (await terminalResponse.json()) as { state: string; result?: unknown };
    expect(["completed", "cancelled"]).toContain(terminal.state);
    expect(cancellationBody.state).toBe(terminal.state);
    if (terminal.state === "completed") {
      expect(completion.value.ok()).toBe(true);
      const completed = (await completion.value.json()) as unknown;
      expect(JSON.stringify(completed) === JSON.stringify(terminal.result)).toBe(true);
      expect(JSON.stringify(cancellationBody.result) === JSON.stringify(terminal.result)).toBe(
        true,
      );
      expect(cancellationCount(confirmation.sale_id)).toBe(0);
      expect(rehearsalFacts(fixture)).toMatchObject({
        sales: 1,
        returns: 0,
        payments: 1,
        checkouts: 1,
        remaining: 57,
        net: "3.00",
      });
    } else {
      expect(completion.value.status()).toBe(409);
      expect(cancellationCount(confirmation.sale_id)).toBe(1);
      expect(rehearsalFacts(fixture)).toEqual(initial);
      const late = await api.post(`${prefix}/complete`, { data: confirmation });
      expect(late.status()).toBe(409);
      expect(rehearsalFacts(fixture)).toEqual(initial);
    }
  } finally {
    await api.dispose();
    await anonymous.dispose();
  }
});
