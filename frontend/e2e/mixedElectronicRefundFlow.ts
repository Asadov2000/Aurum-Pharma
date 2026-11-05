import { type APIRequestContext, type Page } from "@playwright/test";

import { type PostedElectronicRefund } from "../src/features/sales/electronicRefundPosting";
import { type PostedMixedElectronicRefund } from "../src/features/sales/mixedElectronicRefundPosting";

import { dockerExec, E2E_POSTGRES_CONTAINER, E2E_POSTGRES_DB } from "./docker";
import { expect } from "./helpers";
import { verifyRefundAccessRecovery } from "./refundAccessAcceptance";

interface OriginalElectronicSale {
  stock_command_id: string;
  payments: { id: string; method: string; amount: string }[];
  sale_id: string;
  shift_id: string;
  receipt_number: string;
}

export interface AcceptanceStockScope {
  physicalCatalogId: string;
  legacyCatalogId?: string;
}
export function electronicAcceptanceFacts(
  original: OriginalElectronicSale,
  stock: AcceptanceStockScope,
): unknown {
  const ids = [
    original.sale_id,
    original.stock_command_id,
    stock.physicalCatalogId,
    ...(stock.legacyCatalogId ? [stock.legacyCatalogId] : []),
  ];
  ids.forEach((id) => expect(id).toMatch(/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i));
  const physical = stock.physicalCatalogId;
  const legacy = stock.legacyCatalogId ?? stock.physicalCatalogId;
  // SELECT only; dockerExec rejects databases outside the isolated E2E project.
  const sql = `SELECT json_build_object(
    'salePayments',(SELECT count(*) FROM sale_payment WHERE sale_id='${original.sale_id}'),
    'stockCommands',(SELECT count(*) FROM unit_stock_cart_command_v2 WHERE id='${original.stock_command_id}'),
    'physicalRemaining',(SELECT COALESCE(sum(l.closed_packs*(l.packaging->>'units_per_pack')::BIGINT + COALESCE((SELECT sum(p.remaining_units) FROM unit_stock_opened_pack_v2 p WHERE p.lot_id=l.id),0)),0) FROM unit_stock_lot_v2 l WHERE catalog_id='${physical}'),
    'physicalMovements',(SELECT count(*) FROM unit_stock_movement_v2 WHERE lot_id IN (SELECT id FROM unit_stock_lot_v2 WHERE catalog_id='${physical}')),
    'legacyMovements',(SELECT count(*) FROM batch_movement WHERE batch_id IN (SELECT id FROM batch WHERE catalog_id='${legacy}')),
    'legacyRemaining',(SELECT COALESCE(sum(qty_remaining),0)::text FROM batch WHERE catalog_id='${legacy}'),
    'refunds',(SELECT count(*) FROM sale WHERE parent_sale_id='${original.sale_id}' AND sale_type='return'),
    'refundPayments',(SELECT count(*) FROM sale_payment WHERE sale_id IN (SELECT id FROM sale WHERE parent_sale_id='${original.sale_id}' AND sale_type='return')),
    'physicalPostings',(SELECT count(*) FROM pos_unit_electronic_refund_posting WHERE parent_sale_id='${original.sale_id}'),
    'mixedPostings',(SELECT count(*) FROM pos_mixed_electronic_refund_posting WHERE parent_sale_id='${original.sale_id}'),
    'components',(SELECT count(*) FROM pos_unit_refund_payment_component WHERE electronic_refund_id IN (SELECT id FROM pos_unit_electronic_refund_posting WHERE parent_sale_id='${original.sale_id}') OR mixed_electronic_refund_id IN (SELECT id FROM pos_mixed_electronic_refund_posting WHERE parent_sale_id='${original.sale_id}')),
    'physicalQuarantineUnits',(SELECT COALESCE(sum(l.base_units),0) FROM pos_physical_return_line l JOIN pos_physical_return_command c ON c.id=l.command_id AND c.tenant_id=l.tenant_id WHERE c.parent_sale_id='${original.sale_id}'),
    'mixedQuarantineUnits',(SELECT COALESCE(sum(l.base_units),0) FROM pos_mixed_return_line l JOIN pos_mixed_return_command c ON c.id=l.command_id AND c.tenant_id=l.tenant_id WHERE c.parent_sale_id='${original.sale_id}'),
    'mixedQuarantineLegacy',(SELECT COALESCE(sum(l.legacy_quantity),0)::text FROM pos_mixed_return_line l JOIN pos_mixed_return_command c ON c.id=l.command_id AND c.tenant_id=l.tenant_id WHERE c.parent_sale_id='${original.sale_id}'),
    'returnSlices',(SELECT count(*) FROM pos_physical_return_slice s JOIN pos_physical_return_command c ON c.id=s.command_id AND c.tenant_id=s.tenant_id WHERE c.parent_sale_id='${original.sale_id}')+(SELECT count(*) FROM pos_mixed_return_slice s JOIN pos_mixed_return_command c ON c.id=s.command_id AND c.tenant_id=s.tenant_id WHERE c.parent_sale_id='${original.sale_id}'),
    'returnCommands',(SELECT count(*) FROM pos_physical_return_command WHERE parent_sale_id='${original.sale_id}')+(SELECT count(*) FROM pos_mixed_return_command WHERE parent_sale_id='${original.sale_id}')
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

async function openRefund(
  page: Page,
  original: OriginalElectronicSale,
  title: string,
): Promise<void> {
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
  await expect(page.getByRole("dialog", { name: title, exact: true })).toBeVisible();
}

/** Exercise the real sale through its cashier refund entrypoint without repeating a payout. */
export async function verifyElectronicRefundRecovery(
  page: Page,
  api: APIRequestContext,
  original: OriginalElectronicSale,
  legacyMedicine: string | null,
  physicalMedicine: string,
  kind: "physical" | "mixed",
  mode: "card" | "qr" | "split",
  stock: AcceptanceStockScope,
): Promise<void> {
  const title =
    kind === "mixed" ? "Электронный смешанный возврат" : "Электронный физический возврат";
  const journalPrefix =
    kind === "mixed" ? "sales:mixedElectronicRefund:v1:" : "sales:electronicRefund:v1:";
  const endpoint = kind === "mixed" ? "mixed-electronic-refunds" : "unit-electronic-refunds";
  const total = kind === "mixed" ? "1.75" : "0.50";
  const before = electronicAcceptanceFacts(original, stock);
  await openRefund(page, original, title);
  const modal = page.getByRole("dialog", { name: title, exact: true });
  if (kind === "mixed") {
    const legacy = modal.getByRole("group", { name: `Строка 1: ${legacyMedicine}`, exact: true });
    await legacy.getByRole("checkbox").check();
    await legacy.getByLabel(`Количество: ${legacyMedicine}`, { exact: true }).fill("0.125");
  }
  const physical =
    kind === "mixed"
      ? modal.getByRole("group", { name: `Строка 2: ${physicalMedicine}`, exact: true })
      : modal.locator("fieldset").first();
  await physical.getByRole("checkbox").check();
  await physical.getByLabel(`Количество: ${physicalMedicine}`, { exact: true }).fill("1");
  await modal.getByRole("button", { name: "Рассчитать электронный возврат", exact: true }).click();
  await expect(modal.getByText(`Расчёт сервера: ${total} TJS`, { exact: true })).toBeVisible();
  await modal.getByRole("button", { name: "Сохранить выбранный расчёт", exact: true }).click();
  await modal.getByRole("button", { name: "Закрепить расчёт", exact: true }).click();
  await modal.getByRole("button", { name: "Активировать в своей смене", exact: true }).click();
  await expect(modal.getByText(/Это не ответ банка/)).toBeVisible();
  const terminals = modal.getByRole("textbox", { name: /^Терминал / });
  const electronicParts = original.payments.filter((p) => p.method !== "cash");
  await expect(terminals).toHaveCount(electronicParts.length);
  for (let i = 0; i < electronicParts.length; i++) {
    await terminals.first().fill("E2E-REFUND");
    await modal
      .getByRole("textbox", { name: /^Документ / })
      .first()
      .fill(`refund-${crypto.randomUUID()}`);
    await modal
      .getByRole("button", { name: "Сохранить ручные реквизиты", exact: true })
      .first()
      .click();
    await expect(terminals).toHaveCount(electronicParts.length - i - 1);
  }
  if (mode === "split")
    await verifyRefundAccessRecovery(page, {
      parentSaleId: original.sale_id,
      receiptNumber: original.receipt_number,
      title,
      journalPrefix,
      reopen: () => openRefund(page, original, title),
    });
  await modal.getByRole("combobox", { name: /Причина/ }).selectOption("quality_issue");
  await modal
    .getByLabel("Пояснение", { exact: true })
    .fill("Синтетическая проверка смешанного возврата");

  const commands: string[] = [];
  const wrongPaths: string[] = [];
  page.on("request", (request) => {
    if (request.method() !== "POST") return;
    const path = new URL(request.url()).pathname;
    if (path.includes(`/sales/${original.sale_id}/${endpoint}/`)) commands.push(path);
    if (
      path.includes(`/sales/${original.sale_id}/mixed-cash-refunds/`) ||
      path.includes(
        `/sales/${original.sale_id}/${kind === "mixed" ? "unit-electronic-refunds" : "mixed-electronic-refunds"}/`,
      ) ||
      path.includes(`/sales/${original.sale_id}/unit-cash-refunds/`)
    )
      wrongPaths.push(path);
  });
  let posted: PostedMixedElectronicRefund | PostedElectronicRefund | null = null;
  const postingPath = `**/api/v1/sales/${original.sale_id}/${endpoint}/reviews/*/post`;
  await page.route(postingPath, async (route) => {
    const response = await route.fetch();
    expect(response.ok()).toBe(true);
    posted = (await response.json()) as PostedMixedElectronicRefund | PostedElectronicRefund;
    await route.abort("failed");
  });
  await modal.getByRole("button", { name: "Провести возврат", exact: true }).click();
  await expect.poll(() => posted !== null).toBe(true);
  await expect(modal.getByRole("alert")).toBeVisible();
  const saved = posted as PostedMixedElectronicRefund | PostedElectronicRefund | null;
  if (!saved) throw new Error("Saved mixed electronic refund was not observed");
  expect(saved).toMatchObject({
    state: "posted_refund",
    parent_sale_id: original.sale_id,
    total_amount: total,
  });
  expect(saved.receipt.schema_version).toBe(kind === "mixed" ? 4 : 3);
  expect(saved.receipt.document_kind).toBe(
    kind === "mixed" ? "mixed_electronic_refund_receipt" : "physical_electronic_refund_receipt",
  );
  if (kind === "mixed")
    expect(saved.receipt.lines).toMatchObject([
      { inventory_kind: "legacy_pack", quantity: "0.125" },
      { inventory_kind: "physical_unit", quantity: 1 },
    ]);
  else expect(saved.receipt.lines).toMatchObject([{ quantity: 1, base_units: 1 }]);
  expect(saved.receipt.payments.map((p) => p.method).sort()).toEqual(
    original.payments.map((p) => p.method).sort(),
  );
  expect(new Set(saved.receipt.payments.map((p) => p.original_payment_id))).toEqual(
    new Set(original.payments.map((p) => p.id)),
  );
  // Existing allocator floors every part, then assigns all missing cents to
  // the largest fractional remainder (refund_payment_allocation.py).
  const expectedParts =
    mode === "split"
      ? kind === "mixed"
        ? [
            { method: "cash", amount: "0.34" },
            { method: "card", amount: "0.67" },
            { method: "qr", amount: "0.74" },
          ]
        : [
            { method: "cash", amount: "0.09" },
            { method: "card", amount: "0.18" },
            { method: "qr", amount: "0.23" },
          ]
      : [{ method: mode, amount: total }];
  expect(
    saved.receipt.payments
      .map(({ method, amount }) => ({ method, amount }))
      .sort((a, b) => a.method.localeCompare(b.method)),
  ).toEqual(expectedParts.sort((a, b) => a.method.localeCompare(b.method)));
  const cents = (amount: string) => BigInt(amount.replace(".", ""));
  expect(saved.receipt.payments.reduce((sum, p) => sum + cents(p.amount), 0n)).toBe(cents(total));
  for (const part of saved.receipt.payments) {
    const source = original.payments.find((p) => p.id === part.original_payment_id);
    expect(source?.method).toBe(part.method);
    expect(cents(part.amount)).toBeGreaterThan(0n);
    expect(cents(part.amount)).toBeLessThanOrEqual(cents(source!.amount));
  }
  const recoveryEntries = () =>
    page.evaluate(
      (prefix) => Object.entries(localStorage).filter(([key]) => key.startsWith(prefix)),
      journalPrefix,
    );
  await expect.poll(async () => (await recoveryEntries()).length).toBe(1);
  const pending = (await recoveryEntries())[0]![1];
  expect(JSON.parse(pending)).toMatchObject({
    attempted: "post",
    request: { operation_id: saved.operation_id },
  });
  expect(pending).not.toMatch(/terminal|document_number|receipt|cost|comment|E2E-MIXED-REFUND/);
  expect(
    (
      await api.post(`shifts/${original.shift_id}/close`, {
        data: {
          closing_cash_actual: (() => {
            const amount =
              10000n +
              cents(original.payments.find((p) => p.method === "cash")?.amount ?? "0.00") -
              cents(saved.receipt.payments.find((p) => p.method === "cash")?.amount ?? "0.00");
            return `${amount / 100n}.${String(amount % 100n).padStart(2, "0")}`;
          })(),
        },
      })
    ).ok(),
  ).toBe(true);
  const facts = electronicAcceptanceFacts(original, stock);
  expect(facts).toMatchObject({
    refunds: 1,
    refundPayments: original.payments.length,
    components: original.payments.length,
    returnCommands: 1,
    physicalPostings: kind === "physical" ? 1 : 0,
    mixedPostings: kind === "mixed" ? 1 : 0,
    physicalQuarantineUnits: kind === "physical" ? 1 : 0,
    mixedQuarantineUnits: kind === "mixed" ? 1 : 0,
    ...(kind === "mixed" ? { mixedQuarantineLegacy: "0.125" } : {}),
  });
  for (const key of [
    "physicalRemaining",
    "legacyRemaining",
    "legacyMovements",
    "physicalMovements",
    "stockCommands",
    "salePayments",
  ] as const) {
    expect((facts as Record<string, unknown>)[key]).toEqual(
      (before as Record<string, unknown>)[key],
    );
  }
  await page.unroute(postingPath);
  await page.reload();
  await openRefund(page, original, title);
  await expect(
    modal.getByText("Возврат проведён. Сохранённый чек проверен.", { exact: true }),
  ).toBeVisible();
  expect((await recoveryEntries())[0]![1]).toBe(pending);
  expect(commands).toHaveLength(1);
  expect(wrongPaths).toEqual([]);
  const recovered = await api.get(
    `sales/${original.sale_id}/${endpoint}/reviews/${saved.operation_id}/posting`,
  );
  expect(recovered.ok()).toBe(true);
  expect(await recovered.json()).toEqual(saved);
  await modal
    .getByRole("button", { name: "Открыть сохранённый чек возврата", exact: true })
    .click();
  await expect.poll(async () => (await recoveryEntries()).length).toBe(0);
  await page.getByRole("button", { name: "Печать возврата", exact: true }).click();
  const receipt = page.getByRole("dialog", { name: "Печать возврата", exact: true });
  await expect(receipt).toBeVisible();
  if (legacyMedicine)
    await expect(
      page.locator(".receipt-print").getByText(legacyMedicine, { exact: false }),
    ).toBeVisible();
  await expect(
    page.locator(".receipt-print").getByText(physicalMedicine, { exact: false }),
  ).toBeVisible();
  expect(commands).toHaveLength(1);
  expect(wrongPaths).toEqual([]);
  expect(electronicAcceptanceFacts(original, stock)).toEqual(facts);
}
