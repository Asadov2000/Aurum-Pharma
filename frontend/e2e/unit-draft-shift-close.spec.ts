import { request, test, type APIRequestContext } from "@playwright/test";

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

interface Draft {
  id: string;
  shift_id: string;
  status: string;
  items: { id: string }[];
  prepared_cart_items: unknown[];
  payments: unknown[];
}

async function readDraft(api: APIRequestContext, saleId: string): Promise<Draft> {
  const response = await api.get(`sales/${saleId}`);
  expect(response.ok()).toBe(true);
  return (await response.json()) as Draft;
}

function financialFacts(saleId: string, catalogId: string): unknown {
  for (const id of [saleId, catalogId])
    expect(id).toMatch(/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i);
  // SELECT only, restricted by dockerExec to the disposable E2E project.
  const sql = `SELECT json_build_object(
    'payments',(SELECT count(*) FROM sale_payment WHERE sale_id='${saleId}'),
    'lots',(SELECT count(*) FROM unit_stock_lot_v2 WHERE catalog_id='${catalogId}'),
    'movements',(SELECT count(*) FROM unit_stock_movement_v2 m JOIN unit_stock_lot_v2 l ON l.id=m.lot_id WHERE l.catalog_id='${catalogId}')
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

for (const mixed of [false, true]) {
  test(`${mixed ? "mixed becoming pure" : "pure physical"} draft prevents shift close until its last row is removed`, async ({
    page,
  }) => {
    test.setTimeout(120_000);
    clearLoginRateLimit(OWNER.email);
    const anonymous = await request.newContext();
    const tokens = await apiLogin(anonymous, OWNER);
    const api = await apiContext(tokens.access_token);
    try {
      const branch = await seedBranch(api, uniqueName("Unit draft close branch"));
      const register = await seedRegister(api, branch.id, uniqueName("Unit draft close register"));
      const medicine = await seedCatalogItem(api, uniqueName("Unit draft close medicine"), "10.00");
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

      const legacy = mixed
        ? await seedCatalogItem(api, uniqueName("Unit draft legacy medicine"), "10.00")
        : null;
      if (legacy) {
        const supplier = await seedSupplier(api, uniqueName("Unit draft supplier"));
        const expiry = new Date();
        expiry.setUTCDate(expiry.getUTCDate() + 180);
        await seedAcceptedBatch(api, {
          branchId: branch.id,
          supplierId: supplier.id,
          catalogId: legacy.id,
          qty: "5",
          purchasePrice: "5.00",
          salePrice: "10.00",
          expiresAt: expiry.toISOString().slice(0, 10),
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

      await page.getByRole("button", { name: "Выбрать упаковку, блистер или штуки" }).click();
      const editor = page.getByRole("region", {
        name: "Продажа упаковками, блистерами и поштучно",
      });
      await editor.getByLabel("Название лекарства").fill(catalogSearchKey(medicine.brand_name));
      await editor.getByRole("option", { name: new RegExp(medicine.brand_name) }).click();
      await editor.getByRole("radio", { name: "Поштучно", exact: true }).click();
      await editor.getByLabel("Количество, шт.", { exact: true }).fill("2");
      const saved = page.waitForResponse(
        (response) =>
          response.request().method() === "PUT" &&
          /\/sales\/[^/]+\/physical-cart\/items\/[^/]+$/.test(new URL(response.url()).pathname),
      );
      await editor.getByRole("button", { name: "Добавить в корзину", exact: true }).click();
      const written = await saved;
      expect(written.ok()).toBe(true);
      const saleId = ((await written.json()) as { sale_id: string }).sale_id;
      await expect(editor.getByRole("listitem")).toHaveCount(1);
      const draft = await readDraft(api, saleId);
      const before = financialFacts(saleId, medicine.id);
      expect(before).toEqual({ payments: 0, lots: 0, movements: 0 });

      const assertDenied = async () => {
        await expect(
          page.getByRole("button", { name: "Закрыть смену", exact: true }),
        ).toBeDisabled();
        const denied = await api.post(`shifts/${draft.shift_id}/close`, {
          data: { closing_cash_actual: "100.00" },
        });
        expect(denied.status()).toBe(422);
        const current = await api.get("shifts/current", { params: { register_id: register.id } });
        expect(current.ok()).toBe(true);
        expect(await current.json()).toMatchObject({ id: draft.shift_id, status: "open" });
        expect(await readDraft(api, saleId)).toMatchObject({ status: "draft", payments: [] });
        expect(financialFacts(saleId, medicine.id)).toEqual(before);
      };
      await assertDenied();
      if (legacy) {
        expect(draft.items).toHaveLength(1);
        // The physical editor deliberately locks legacy edits. A second API client
        // removes that row, then reload verifies the remaining pure physical draft.
        const removed = await api.delete(`sales/${saleId}/items/${draft.items[0]!.id}`, {
          data: { operation_id: crypto.randomUUID() },
        });
        expect(removed.ok()).toBe(true);
        await page.reload();
        await expect(editor.getByRole("listitem")).toHaveCount(1);
        expect((await readDraft(api, saleId)).items).toEqual([]);
        await assertDenied();
      }

      await editor.getByRole("button", { name: "Удалить", exact: true }).click();
      await expect(editor.getByRole("listitem")).toHaveCount(0);
      // An editor opened explicitly in this session stays open when emptied.
      // After reload it is shown only because rows exist, so deleting the last
      // row already returns the mixed-to-pure case to the ordinary sale view.
      if (!mixed) await page.getByRole("button", { name: "Вернуться к обычной продаже" }).click();
      await expect(editor).toHaveCount(0);
      const empty = await readDraft(api, saleId);
      expect(empty.items).toEqual([]);
      expect(empty.prepared_cart_items).toEqual([]);
      expect(empty.payments).toEqual([]);
      await expect(page.getByRole("button", { name: "Закрыть смену", exact: true })).toBeEnabled();
      await page.getByRole("button", { name: "Закрыть смену", exact: true }).click();
      const dialog = page.getByRole("dialog");
      await dialog.getByLabel("Наличные после пересчёта").fill("100");
      const closed = page.waitForResponse(
        (response) =>
          response.request().method() === "POST" &&
          new URL(response.url()).pathname.endsWith(`/shifts/${draft.shift_id}/close`),
      );
      await dialog.getByRole("button", { name: "Подтвердить закрытие смены" }).click();
      const result = await closed;
      expect(result.ok()).toBe(true);
      expect(await result.json()).toMatchObject({ status: "closed", closing_difference: "0.00" });
      await expect(page.getByLabel("Наличные в кассе на начало смены")).toBeVisible();
      expect(financialFacts(saleId, medicine.id)).toEqual(before);
    } finally {
      await api.dispose();
      await anonymous.dispose();
    }
  });
}
