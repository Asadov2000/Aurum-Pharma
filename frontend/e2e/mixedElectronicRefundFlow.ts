import { type APIRequestContext, type Page } from "@playwright/test";

import { type PostedMixedElectronicRefund } from "../src/features/sales/mixedElectronicRefundPosting";

import { dockerExec, E2E_POSTGRES_CONTAINER, E2E_POSTGRES_DB } from "./docker";
import { expect } from "./helpers";

interface OriginalMixedSale {
  sale_id: string;
  shift_id: string;
  receipt_number: string;
}

function persistedRefundFacts(parentId: string, saleId: string, operationId: string): unknown {
  for (const id of [parentId, saleId, operationId])
    expect(id).toMatch(/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i);
  // dockerExec refuses any target outside the launcher's disposable E2E project.
  const query = `SELECT json_build_object(
    'postings', (SELECT count(*) FROM pos_mixed_electronic_refund_posting WHERE parent_sale_id='${parentId}'),
    'refunds', (SELECT count(*) FROM sale WHERE parent_sale_id='${parentId}' AND sale_type='return'),
    'payments', (SELECT count(*) FROM sale_payment WHERE sale_id='${saleId}'),
    'components', (SELECT count(*) FROM pos_unit_refund_payment_component WHERE mixed_electronic_refund_id='${operationId}'),
    'journal', (SELECT count(*) FROM pos_mixed_return_command WHERE parent_sale_id='${parentId}'),
    'lines', (SELECT count(*) FROM pos_mixed_return_line l JOIN pos_mixed_return_command c ON c.id=l.command_id WHERE c.parent_sale_id='${parentId}')
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
      query,
    ]).trim(),
  ) as unknown;
}

async function openMixedRefund(page: Page, original: OriginalMixedSale): Promise<void> {
  await page.goto("/sales");
  const filtered = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return (
      response.request().method() === "GET" &&
      url.pathname === "/api/v1/sales" &&
      url.searchParams.get("receipt_number") === original.receipt_number
    );
  });
  await page.getByLabel("№ чека").fill(original.receipt_number);
  expect((await filtered).ok()).toBe(true);
  await page
    .getByRole("button", { name: `Открыть чек № ${original.receipt_number}`, exact: true })
    .first()
    .click();
  await page
    .getByRole("dialog", { name: `Чек № ${original.receipt_number}`, exact: true })
    .getByRole("button", { name: "Оформить возврат", exact: true })
    .click();
  await expect(
    page.getByRole("dialog", { name: "Электронный смешанный возврат", exact: true }),
  ).toBeVisible();
}

/** Continue the real mixed card checkout scenario through the existing cashier entrypoint. */
export async function verifyMixedElectronicRefundRecovery(
  page: Page,
  api: APIRequestContext,
  original: OriginalMixedSale,
  legacyMedicine: string,
  physicalMedicine: string,
): Promise<void> {
  await openMixedRefund(page, original);
  const modal = page.getByRole("dialog", { name: "Электронный смешанный возврат", exact: true });
  const legacy = modal.getByRole("group", { name: `Строка 1: ${legacyMedicine}`, exact: true });
  const physical = modal.getByRole("group", { name: `Строка 2: ${physicalMedicine}`, exact: true });
  await legacy.getByRole("checkbox").check();
  await legacy.getByLabel(`Количество: ${legacyMedicine}`, { exact: true }).fill("0.125");
  await physical.getByRole("checkbox").check();
  await physical.getByLabel(`Количество: ${physicalMedicine}`, { exact: true }).fill("1");
  await modal.getByRole("button", { name: "Рассчитать электронный возврат", exact: true }).click();
  await expect(modal.getByText("Расчёт сервера: 1.75 TJS", { exact: true })).toBeVisible();
  await modal.getByRole("button", { name: "Сохранить выбранный расчёт", exact: true }).click();
  await modal.getByRole("button", { name: "Закрепить расчёт", exact: true }).click();
  await modal.getByRole("button", { name: "Активировать в своей смене", exact: true }).click();
  await expect(modal.getByText(/Это не ответ банка/)).toBeVisible();
  await modal.getByRole("textbox", { name: /^Терминал / }).fill("E2E-MIXED-REFUND");
  await modal.getByRole("textbox", { name: /^Документ / }).fill(`refund-${crypto.randomUUID()}`);
  await modal.getByRole("button", { name: "Сохранить ручные реквизиты", exact: true }).click();
  await expect(modal.getByRole("textbox", { name: /^Терминал / })).toHaveCount(0);
  await modal.getByRole("combobox", { name: /Причина/ }).selectOption("quality_issue");
  await modal
    .getByLabel("Пояснение", { exact: true })
    .fill("Синтетическая проверка смешанного возврата");

  const commands: string[] = [];
  const wrongPaths: string[] = [];
  page.on("request", (request) => {
    if (request.method() !== "POST") return;
    const path = new URL(request.url()).pathname;
    if (path.includes(`/sales/${original.sale_id}/mixed-electronic-refunds/`)) commands.push(path);
    if (
      path.includes(`/sales/${original.sale_id}/mixed-cash-refunds/`) ||
      path.includes(`/sales/${original.sale_id}/unit-electronic-refunds/`) ||
      path.includes(`/sales/${original.sale_id}/unit-cash-refunds/`)
    )
      wrongPaths.push(path);
  });
  let posted: PostedMixedElectronicRefund | null = null;
  const postingPath = `**/api/v1/sales/${original.sale_id}/mixed-electronic-refunds/reviews/*/post`;
  await page.route(postingPath, async (route) => {
    const response = await route.fetch();
    expect(response.ok()).toBe(true);
    posted = (await response.json()) as PostedMixedElectronicRefund;
    await route.abort("failed");
  });
  await modal.getByRole("button", { name: "Провести возврат", exact: true }).click();
  await expect.poll(() => posted !== null).toBe(true);
  await expect(modal.getByRole("alert")).toBeVisible();
  const saved = posted as PostedMixedElectronicRefund | null;
  if (!saved) throw new Error("Saved mixed electronic refund was not observed");
  expect(saved).toMatchObject({
    state: "posted_refund",
    parent_sale_id: original.sale_id,
    total_amount: "1.75",
    receipt: {
      schema_version: 4,
      document_kind: "mixed_electronic_refund_receipt",
      lines: [
        { inventory_kind: "legacy_pack", quantity: "0.125" },
        { inventory_kind: "physical_unit", quantity: 1 },
      ],
      payments: [{ method: "card", amount: "1.75" }],
    },
  });
  const recoveryEntries = () =>
    page.evaluate(() =>
      Object.entries(localStorage).filter(([key]) =>
        key.startsWith("sales:mixedElectronicRefund:v1:"),
      ),
    );
  await expect.poll(async () => (await recoveryEntries()).length).toBe(1);
  const pending = (await recoveryEntries())[0]![1];
  expect(JSON.parse(pending)).toMatchObject({
    attempted: "post",
    request: {
      operation_id: saved.operation_id,
      request: {
        selection: {
          items: [
            { inventory_kind: "legacy_pack", quantity: "0.125" },
            { inventory_kind: "physical_unit", quantity: 1 },
          ],
        },
      },
    },
  });
  expect(pending).not.toMatch(/terminal|document_number|receipt|cost|comment|E2E-MIXED-REFUND/);
  expect(
    (
      await api.post(`shifts/${original.shift_id}/close`, {
        data: { closing_cash_actual: "100.00" },
      })
    ).ok(),
  ).toBe(true);
  const facts = persistedRefundFacts(original.sale_id, saved.sale_id, saved.operation_id);
  expect(facts).toEqual({
    postings: 1,
    refunds: 1,
    payments: 1,
    components: 1,
    journal: 1,
    lines: 2,
  });
  await page.unroute(postingPath);
  await page.reload();
  await openMixedRefund(page, original);
  await expect(
    modal.getByText("Возврат проведён. Сохранённый чек проверен.", { exact: true }),
  ).toBeVisible();
  expect((await recoveryEntries())[0]![1]).toBe(pending);
  expect(commands).toHaveLength(1);
  expect(wrongPaths).toEqual([]);
  await modal
    .getByRole("button", { name: "Открыть сохранённый чек возврата", exact: true })
    .click();
  await expect.poll(async () => (await recoveryEntries()).length).toBe(0);
  await page.getByRole("button", { name: "Печать возврата", exact: true }).click();
  const receipt = page.getByRole("dialog", { name: "Печать возврата", exact: true });
  await expect(receipt).toBeVisible();
  await expect(
    page.locator(".receipt-print").getByText(legacyMedicine, { exact: false }),
  ).toBeVisible();
  await expect(
    page.locator(".receipt-print").getByText(physicalMedicine, { exact: false }),
  ).toBeVisible();
  expect(commands).toHaveLength(1);
  expect(wrongPaths).toEqual([]);
  expect(persistedRefundFacts(original.sale_id, saved.sale_id, saved.operation_id)).toEqual(facts);
}
