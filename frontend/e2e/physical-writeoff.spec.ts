import { request, test, type APIRequestContext, type Page } from "@playwright/test";

import {
  apiContext,
  apiLogin,
  clearLoginRateLimit,
  expect,
  loginInBrowser,
  OWNER,
  seedAcceptedBatch,
  seedBranch,
  seedCatalogItem,
  seedSupplier,
  uniqueName,
} from "./helpers";

interface Lot {
  lot_id: string;
  branch_id: string;
  base_units: string;
  closed_packs: string;
  opened_units: string;
}

async function readLot(api: APIRequestContext, branchId: string): Promise<Lot> {
  const response = await api.get("batches/physical", {
    params: { branch_id: branchId, show_empty: true },
  });
  expect(response.ok()).toBe(true);
  const result = (await response.json()) as { items: Lot[]; total: number };
  expect(result.total).toBe(1);
  const lot = result.items[0];
  if (!lot) throw new Error("Physical fixture did not yield a lot");
  return lot;
}

async function receiveSealedStock(
  api: APIRequestContext,
  branchId: string,
  catalogId: string,
  expiresAt: string,
): Promise<void> {
  const supplier = await seedSupplier(api, uniqueName("Write-off supplier"));
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
      expires_at: expiresAt,
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
  const revision = (await checked.json()) as { acceptance_revision: string };
  expect(
    (
      await api.post(`incoming/${document.id}/accept`, {
        data: { stock_mode: "physical_unit_v2", expected_revision: revision.acceptance_revision },
      })
    ).ok(),
  ).toBe(true);
}

async function receiveOpenedStock(
  api: APIRequestContext,
  branchId: string,
  catalogId: string,
  expiresAt: string,
): Promise<string> {
  const supplier = await seedSupplier(api, uniqueName("Opened write-off supplier"));
  const legacy = await seedAcceptedBatch(api, {
    branchId,
    supplierId: supplier.id,
    catalogId,
    qty: "0.750",
    purchasePrice: "5.00",
    salePrice: "10.00",
    expiresAt,
    batchNumber: uniqueName("Opened write-off"),
  });
  const reconciliationId = crypto.randomUUID();
  const packId = crypto.randomUUID();
  const openedAt = new Date();
  openedAt.setUTCDate(openedAt.getUTCDate() - 1);
  const saved = await api.post(`batches/${legacy.id}/unit-reconciliation/records`, {
    data: {
      branch_id: branchId,
      reconciliation_id: reconciliationId,
      expected_legacy_quantity: "0.750",
      packaging: {
        version: 2,
        unit: "tablet",
        units_per_pack: 20,
        units_per_blister: 10,
        dispense_step: 1,
      },
      closed_packs: 0,
      opened_packs: [
        {
          id: packId,
          opened_at: openedAt.toISOString(),
          intact_blisters: [1],
          opened_blisters: [{ number: 2, remaining_units: 5 }],
          loose_units: 0,
        },
      ],
    },
  });
  expect(saved.ok()).toBe(true);
  const transferred = await api.post(`batches/${legacy.id}/unit-transition`, {
    data: {
      branch_id: branchId,
      reconciliation_id: reconciliationId,
      command_id: crypto.randomUUID(),
    },
  });
  expect(transferred.ok()).toBe(true);
  return packId;
}

async function openPhysicalInventory(page: Page, branchId: string): Promise<void> {
  await page.goto("/batches");
  await page.getByRole("button", { name: "Поштучные партии", exact: true }).click();
  const panel = page.getByRole("region", { name: "Поштучные партии", exact: true });
  await panel.getByLabel("Аптека", { exact: true }).selectOption(branchId);
  await panel.getByRole("button", { name: "Показать партии", exact: true }).click();
}

for (const scenario of [
  { name: "sealed packs", opened: false, lostResponse: false, before: "80", after: "60" },
  {
    name: "lost response after reload",
    opened: false,
    lostResponse: true,
    before: "80",
    after: "60",
  },
  { name: "explicit opened blister", opened: true, lostResponse: false, before: "15", after: "11" },
]) {
  test(`physical write-off ${scenario.name} preserves explicit stock and operation identity`, async ({
    page,
  }) => {
    test.setTimeout(180_000);
    clearLoginRateLimit(OWNER.email);
    const anonymous = await request.newContext();
    const tokens = await apiLogin(anonymous, OWNER);
    const api = await apiContext(tokens.access_token);
    try {
      const branch = await seedBranch(api, uniqueName("Write-off branch"));
      const product = await seedCatalogItem(api, uniqueName("Write-off tablets"), "10.00");
      expect(
        (
          await api.patch(`catalog/${product.id}`, {
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
      const expiry = new Date();
      expiry.setUTCDate(expiry.getUTCDate() + 180);
      const expiresAt = expiry.toISOString().slice(0, 10);
      const packId = scenario.opened
        ? await receiveOpenedStock(api, branch.id, product.id, expiresAt)
        : undefined;
      if (!scenario.opened) await receiveSealedStock(api, branch.id, product.id, expiresAt);
      const initial = await readLot(api, branch.id);
      expect(initial).toMatchObject({ branch_id: branch.id, base_units: scenario.before });
      await page.setViewportSize({ width: 1366, height: 900 });
      await loginInBrowser(page, OWNER);
      await openPhysicalInventory(page, branch.id);
      const panel = page.getByRole("region", { name: "Поштучные партии", exact: true });
      await expect(panel.getByText(`${scenario.before} табл.`, { exact: true })).toBeVisible();
      const operations: string[] = [];
      const selections: unknown[] = [];
      const recoveries: string[] = [];
      page.on("request", (req) => {
        if (
          req.method() === "GET" &&
          req.url().includes("/batches/physical/write-offs/operations/")
        ) {
          recoveries.push(new URL(req.url()).pathname.split("/").at(-1) ?? "");
        }
      });
      await page.route("**/api/v1/batches/physical/write-offs", async (route) => {
        if (route.request().method() !== "POST") return route.continue();
        const submitted = route.request().postDataJSON() as {
          operation_id: string;
          selection: unknown;
        };
        operations.push(submitted.operation_id);
        selections.push(submitted.selection);
        if (!scenario.lostResponse) return route.continue();
        const response = await route.fetch();
        expect(response.ok()).toBe(true);
        await route.abort("failed");
      });
      await panel.getByRole("button", { name: "Списать", exact: true }).click();
      const dialog = page.getByRole("dialog", { name: "Списание поштучного остатка", exact: true });
      await dialog.getByLabel("Причина", { exact: true }).selectOption("damaged");
      if (scenario.opened) {
        expect(packId).toBeDefined();
        await dialog.getByLabel("Закрытые упаковки", { exact: true }).fill("0");
        // The selection labels are bound to the real pack and blister identities.
        await dialog.getByLabel(`Блистер 2 · ${packId}`, { exact: true }).fill("4");
      } else {
        await dialog.getByLabel("Закрытые упаковки", { exact: true }).fill("1");
      }
      expect(operations).toHaveLength(0);
      await dialog.getByRole("button", { name: "Проверить списание", exact: true }).click();
      await expect(
        page.getByRole("button", { name: "Подтвердить списание", exact: true }),
      ).toBeVisible();
      expect(operations).toHaveLength(0);
      await page.getByRole("button", { name: "Подтвердить списание", exact: true }).click();
      if (scenario.lostResponse) {
        await expect(
          panel.getByRole("region", { name: "Результат списания", exact: true }).getByRole("alert"),
        ).toBeVisible();
        expect(operations).toHaveLength(1);
        await page.reload();
        await openPhysicalInventory(page, branch.id);
        await page
          .getByRole("region", { name: "Результат списания", exact: true })
          .getByRole("button", { name: "Проверить результат", exact: true })
          .click();
      }
      await expect(page.getByText("Списание выполнено.", { exact: true })).toBeVisible();
      await expect(panel.getByText(`${scenario.after} табл.`, { exact: true })).toBeVisible();
      expect(operations).toHaveLength(1);
      expect(selections[0]).toEqual(
        scenario.opened
          ? {
              closed_packs: 0,
              opened_packs: [
                { pack_id: packId, blisters: [{ number: 2, units: 4 }], loose_units: 0 },
              ],
            }
          : { closed_packs: 1, opened_packs: [] },
      );
      if (scenario.lostResponse) {
        expect(recoveries.length).toBeGreaterThan(0);
        expect(recoveries.every((operationId) => operationId === operations[0])).toBe(true);
      }
      const after = await readLot(api, branch.id);
      expect(after).toMatchObject({
        lot_id: initial.lot_id,
        branch_id: branch.id,
        base_units: scenario.after,
      });
      const recovered = await api.get(`batches/physical/write-offs/operations/${operations[0]}`, {
        params: { branch_id: branch.id },
      });
      expect(recovered.ok()).toBe(true);
      expect(await recovered.json()).toMatchObject({
        operation_id: operations[0],
        branch_id: branch.id,
        lot_id: initial.lot_id,
        base_units: scenario.opened ? "4" : "20",
        closed_packs: scenario.opened ? "0" : "1",
      });
      await page.getByRole("button", { name: "Готово", exact: true }).click();
      await expect(
        panel.getByRole("region", { name: "Результат списания", exact: true }),
      ).toHaveCount(0);
      await expect(panel.getByRole("button", { name: "Списать", exact: true })).toBeEnabled();
      expect(operations).toHaveLength(1);
    } finally {
      await api.dispose();
      await anonymous.dispose();
    }
  });
}
