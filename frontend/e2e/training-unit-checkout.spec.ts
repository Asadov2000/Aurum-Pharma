import { request, test, type APIRequestContext, type Page } from "@playwright/test";

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

interface TrainingResult {
  document_kind: "training_checkout";
  is_test: true;
  sale_id: string;
  operation_id: string;
  shift_id: string;
  receipt_number: string;
  total_amount: string;
  receipt: {
    document_kind: "training_sale_receipt";
    is_test: true;
    receipt_number: string;
    cash_tendered: string;
    change_amount: string;
    lines: {
      inventory_kind: "legacy_pack" | "physical_unit";
      sale_format?: string;
      quantity: string | number;
      base_units?: number;
    }[];
    simulated_payments: { method: string; amount: string }[];
  };
}

function sql(query: string): string {
  // dockerExec validates both immutable container IDs and their disposable project.
  return dockerExec(E2E_POSTGRES_CONTAINER, [
    "psql",
    "-U",
    "postgres",
    "-d",
    E2E_POSTGRES_DB,
    "-At",
    "-c",
    query,
  ]).trim();
}

function checkedId(value: string): string {
  expect(value).toMatch(/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i);
  return value;
}

function realEffects(saleId: string, catalogIds: string[]): unknown {
  checkedId(saleId);
  const catalogs = catalogIds.map((id) => `'${checkedId(id)}'`).join(",");
  // Completion may save its TEST document and receipt sequence, but may not
  // create payment/stock commands or change either inventory's persisted rows.
  return JSON.parse(
    sql(`SELECT jsonb_build_object(
      'payments',(SELECT count(*) FROM sale_payment WHERE sale_id='${saleId}'),
      'attempts',(SELECT count(*) FROM pos_payment_attempt WHERE sale_id='${saleId}'),
      'physicalCheckouts',(SELECT count(*) FROM pos_physical_checkout WHERE sale_id='${saleId}'),
      'stockCommands',(SELECT count(*) FROM unit_stock_cart_command_v2 WHERE checkout_sale_id='${saleId}'),
      'physicalLots',(SELECT jsonb_agg(to_jsonb(l) ORDER BY l.id)
        FROM unit_stock_lot_v2 l WHERE l.catalog_id IN (${catalogs})),
      'openedPacks',(SELECT jsonb_agg(to_jsonb(p) ORDER BY p.id)
        FROM unit_stock_opened_pack_v2 p WHERE p.lot_id IN
          (SELECT id FROM unit_stock_lot_v2 WHERE catalog_id IN (${catalogs}))),
      'physicalMovements',(SELECT jsonb_agg(to_jsonb(m) ORDER BY m.id)
        FROM unit_stock_movement_v2 m WHERE m.lot_id IN
          (SELECT id FROM unit_stock_lot_v2 WHERE catalog_id IN (${catalogs}))),
      'legacyBatches',(SELECT jsonb_agg(to_jsonb(b) ORDER BY b.id)
        FROM batch b WHERE b.catalog_id IN (${catalogs})),
      'legacyMovements',(SELECT jsonb_agg(to_jsonb(m) ORDER BY m.id)
        FROM batch_movement m WHERE m.batch_id IN
          (SELECT id FROM batch WHERE catalog_id IN (${catalogs})))
    );`),
  ) as unknown;
}

async function receivePhysicalStock(api: APIRequestContext, branchId: string, catalogId: string) {
  const supplier = await seedSupplier(api, uniqueName("Training physical supplier"));
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
      expires_at: isoDateInDays(180),
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
  const preview = await api.get(`incoming/${document.id}`);
  expect(preview.ok()).toBe(true);
  const revision = (await preview.json()) as { acceptance_revision: string };
  expect(
    (
      await api.post(`incoming/${document.id}/accept`, {
        data: { stock_mode: "physical_unit_v2", expected_revision: revision.acceptance_revision },
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
  return checkedId(saleId);
}

for (const kind of ["physical", "mixed"] as const) {
  test(`recovers saved ${kind} training checkout and TEST receipt without real effects`, async ({
    page,
  }) => {
    test.setTimeout(120_000);
    clearLoginRateLimit(OWNER.email);
    const anonymous = await request.newContext();
    const tokens = await apiLogin(anonymous, OWNER);
    const api = await apiContext(tokens.access_token);
    try {
      const branch = await seedBranch(api, uniqueName("Training checkout branch"));
      const register = await seedRegister(api, branch.id, uniqueName("Training checkout register"));
      const medicine = await seedCatalogItem(
        api,
        uniqueName("Training physical medicine"),
        "10.00",
      );
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
      const legacy =
        kind === "mixed"
          ? await seedCatalogItem(api, uniqueName("Training legacy medicine"), "10.00")
          : null;
      if (legacy) {
        const supplier = await seedSupplier(api, uniqueName("Training legacy supplier"));
        await seedAcceptedBatch(api, {
          branchId: branch.id,
          supplierId: supplier.id,
          catalogId: legacy.id,
          qty: "4",
          purchasePrice: "5.00",
          salePrice: "10.00",
          expiresAt: isoDateInDays(180),
        });
      }
      await page.setViewportSize({ width: 1366, height: 768 });
      await loginInBrowser(page, OWNER);
      await page.goto("/pos");
      await page.getByLabel(/^Касса$/).selectOption({ label: register.name });
      await page.getByLabel("Наличные в кассе на начало смены").fill("100");
      await page.getByRole("button", { name: "Открыть смену" }).click();
      await expect(page.getByText("Смена открыта")).toBeVisible();
      if (legacy)
        await addPosItemToCart(page, {
          brandName: legacy.brand_name,
          qty: "1",
          expectedCartItems: 1,
        });
      const saleId = await addPhysicalFormats(page, medicine.brand_name);
      // The isolated seed's OWNER tenant is active. Match the established backend
      // training_source fixture by marking only this new draft, never the tenant.
      // This test proves dispatch from persisted is_test, not setup-tenant creation.
      expect(
        sql(`UPDATE sale SET is_test=true WHERE id='${saleId}'
        AND status='draft' AND register_id='${checkedId(register.id)}' RETURNING id;`),
      ).toContain(saleId);
      const draft = await api.get(`sales/${saleId}`);
      expect(draft.ok()).toBe(true);
      expect(await draft.json()).toMatchObject({ id: saleId, status: "draft", is_test: true });
      const catalogs = [medicine.id, ...(legacy ? [legacy.id] : [])];
      const before = realEffects(saleId, catalogs);
      expect(before).toMatchObject({
        payments: 0,
        attempts: 0,
        physicalCheckouts: 0,
        stockCommands: 0,
      });
      const realWrites: string[] = [];
      page.on("request", (outgoing) => {
        if (["GET", "HEAD", "OPTIONS"].includes(outgoing.method())) return;
        const pathname = new URL(outgoing.url()).pathname;
        if (
          /\/sales\/checkout$|\/sales\/[^/]+\/(?:payments|complete)$|\/payment-attempts(?:\/|$)|\/physical-cash\/|\/mixed-cash\/|\/unit-payment-plan\//.test(
            pathname,
          )
        )
          realWrites.push(pathname);
      });
      await page.reload();
      const panel = page.getByRole("region", { name: "Учебная продажа", exact: true });
      await expect(panel).toBeVisible();
      await panel.getByRole("button", { name: "Рассчитать учебный чек", exact: true }).click();
      const total = kind === "mixed" ? "26.00" : "16.00";
      const tendered = kind === "mixed" ? "30.00" : "20.00";
      await panel.getByLabel("Учебные наличные, TJS", { exact: true }).fill(total);
      await panel.getByLabel("Учебно получено наличными, TJS", { exact: true }).fill(tendered);
      let completed: TrainingResult | null = null;
      let blockStatus = false;
      const commands: string[] = [];
      const recoveryReads: string[] = [];
      await page.route(`**/api/v1/sales/${saleId}/training/operations/*`, async (route) => {
        expect(route.request().method()).toBe("GET");
        recoveryReads.push(new URL(route.request().url()).pathname);
        if (blockStatus) await route.abort("failed");
        else await route.continue();
      });
      await page.route(`**/api/v1/sales/${saleId}/training/complete`, async (route) => {
        expect(route.request().method()).toBe("POST");
        commands.push((route.request().postDataJSON() as { operation_id: string }).operation_id);
        const response = await route.fetch();
        expect(response.ok()).toBe(true);
        completed = (await response.json()) as TrainingResult;
        blockStatus = true;
        await route.abort("failed");
      });
      await panel.getByRole("button", { name: "Завершить учебную продажу", exact: true }).click();
      await expect.poll(() => completed !== null).toBe(true);
      const saved = completed as TrainingResult | null;
      if (!saved) throw new Error("Saved training response not observed");
      expect(saved).toMatchObject({
        document_kind: "training_checkout",
        is_test: true,
        sale_id: saleId,
        total_amount: total,
      });
      expect(saved.receipt_number).toMatch(/^TEST-/);
      expect(saved.receipt).toMatchObject({
        document_kind: "training_sale_receipt",
        is_test: true,
        receipt_number: saved.receipt_number,
        simulated_payments: [{ method: "cash", amount: total }],
        cash_tendered: tendered,
        change_amount: "4.00",
      });
      expect(new Set(saved.receipt.lines.map((line) => line.inventory_kind))).toEqual(
        new Set(kind === "mixed" ? ["legacy_pack", "physical_unit"] : ["physical_unit"]),
      );
      expect(
        saved.receipt.lines
          .filter((line) => line.inventory_kind === "physical_unit")
          .map(({ sale_format, quantity, base_units }) => ({ sale_format, quantity, base_units })),
      ).toEqual([
        { sale_format: "unit", quantity: 2, base_units: 2 },
        { sale_format: "blister", quantity: 1, base_units: 10 },
        { sale_format: "pack", quantity: 1, base_units: 20 },
      ]);
      expect(realEffects(saleId, catalogs)).toEqual(before);
      blockStatus = false;
      const postsAfterReload: string[] = [];
      page.on("request", (outgoing) => {
        if (outgoing.method() === "POST") postsAfterReload.push(new URL(outgoing.url()).pathname);
      });
      await page.reload();
      // Mounting the pending panel recovers via GET; it must never replay a POST.
      await expect(
        panel.getByRole("button", { name: "Открыть TEST-чек", exact: true }),
      ).toBeVisible();
      await expect(panel.getByText("Учебная сдача: 4.00 TJS", { exact: true })).toBeVisible();
      expect(recoveryReads).toContain(
        `/api/v1/sales/${saleId}/training/operations/${saved.operation_id}`,
      );
      await panel.getByRole("button", { name: "Открыть TEST-чек", exact: true }).click();
      const printDialog = page.getByRole("dialog", { name: "Печать чека", exact: true });
      await expect(printDialog).toBeVisible();
      await expect(
        printDialog.getByRole("button", { name: "Печать чека", exact: true }),
      ).toBeEnabled();
      await expect(
        page.locator(".receipt-print").getByText(`Чек № ${saved.receipt_number}`, { exact: true }),
      ).toBeVisible();
      const details = await api.get(`sales/${saleId}`);
      expect(details.ok()).toBe(true);
      expect(await details.json()).toMatchObject({
        status: "completed",
        is_test: true,
        receipt_number: saved.receipt_number,
        payments: [],
      });
      const recovery = await api.get(`sales/${saleId}/training/operations/${saved.operation_id}`);
      expect(recovery.ok()).toBe(true);
      expect(await recovery.json()).toMatchObject({ state: "completed", result: saved });
      expect(sql(`SELECT count(*) FROM pos_training_checkout WHERE sale_id='${saleId}';`)).toBe(
        "1",
      );
      expect(commands).toEqual([saved.operation_id]);
      expect(postsAfterReload).toEqual([]);
      expect(realWrites).toEqual([]);
      expect(realEffects(saleId, catalogs)).toEqual(before);
    } finally {
      await api.dispose();
      await anonymous.dispose();
    }
  });
}

function isoDateInDays(days: number): string {
  const value = new Date();
  value.setUTCDate(value.getUTCDate() + days);
  return value.toISOString().slice(0, 10);
}
