import { request, test } from "@playwright/test";
import {
  apiContext,
  apiLogin,
  clearLoginRateLimit,
  expect,
  loginInBrowser,
  OWNER,
  seedBranch,
  seedCatalogItem,
  seedSupplier,
  uniqueName,
} from "./helpers";
import { type APIRequestContext } from "@playwright/test";

async function receivePhysicalStock(
  api: APIRequestContext,
  branchId: string,
  catalogId: string,
  expiresAt: string,
): Promise<void> {
  const supplier = await seedSupplier(api, uniqueName("Decision physical supplier"));
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
  const preview = (await checked.json()) as { acceptance_revision: string };
  expect(
    (
      await api.post(`incoming/${document.id}/accept`, {
        data: { stock_mode: "physical_unit_v2", expected_revision: preview.acceptance_revision },
      })
    ).ok(),
  ).toBe(true);
}

interface Lot {
  lot_id: string;
  branch_id: string;
  is_blocked: boolean;
  blocking_revision: string;
  base_units: string;
  closed_packs: string;
  opened_units: string;
}

test("physical lot blocking survives lost response and rejects stale confirmation", async ({
  page,
}) => {
  test.setTimeout(180_000);
  clearLoginRateLimit(OWNER.email);
  const anonymous = await request.newContext();
  const tokens = await apiLogin(anonymous, OWNER);
  const api = await apiContext(tokens.access_token);
  try {
    const branch = await seedBranch(api, uniqueName("Physical lot branch"));
    const product = await seedCatalogItem(api, uniqueName("Physical lot tablets"), "10.00");
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
    await receivePhysicalStock(api, branch.id, product.id, expiry.toISOString().slice(0, 10));
    const readLot = async (): Promise<Lot> => {
      const response = await api.get("batches/physical", { params: { branch_id: branch.id } });
      expect(response.ok()).toBe(true);
      const data = (await response.json()) as { items: Lot[]; total: number };
      expect(data.total).toBe(1);
      return data.items[0];
    };
    const initial = await readLot();
    expect(initial).toMatchObject({
      base_units: "80",
      closed_packs: "4",
      opened_units: "0",
      is_blocked: false,
    });
    await page.setViewportSize({ width: 1366, height: 768 });
    await loginInBrowser(page, OWNER);
    await page.goto("/batches");
    await page.getByRole("button", { name: "Поштучные партии", exact: true }).click();
    const panel = page.getByRole("region", { name: "Поштучные партии", exact: true });
    await panel.getByLabel("Аптека", { exact: true }).selectOption(branch.id);
    await panel.getByRole("button", { name: "Показать партии", exact: true }).click();
    await expect(panel.getByText("80 табл.", { exact: true })).toBeVisible();
    let writes = 0;
    await page.route(`**/api/v1/batches/physical/${initial.lot_id}/blocking`, async (route) => {
      if (route.request().method() !== "PATCH") return route.continue();
      writes += 1;
      if (writes !== 1) return route.continue();
      const result = await route.fetch();
      expect(result.ok()).toBe(true);
      await route.abort("failed");
    });
    await panel.getByRole("button", { name: "Заблокировать", exact: true }).click();
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "Подтвердить", exact: true })
      .click();
    await expect(panel.getByText(/Проверьте состояние партии/)).toBeVisible();
    await expect(panel.getByRole("button", { name: "Разблокировать", exact: true })).toBeEnabled();
    expect(writes).toBe(1);
    const blocked = await readLot();
    expect(blocked).toMatchObject({
      base_units: "80",
      closed_packs: "4",
      opened_units: "0",
      is_blocked: true,
    });
    // A concurrent user changes state after this UI has loaded the revision.
    expect(
      (
        await api.patch(`batches/physical/${initial.lot_id}/blocking`, {
          data: {
            branch_id: branch.id,
            is_blocked: false,
            expected_blocking_revision: blocked.blocking_revision,
          },
        })
      ).ok(),
    ).toBe(true);
    await panel.getByRole("button", { name: "Разблокировать", exact: true }).click();
    const stale = page.waitForResponse(
      (response) =>
        response.url().endsWith(`/batches/physical/${initial.lot_id}/blocking`) &&
        response.request().method() === "PATCH",
    );
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "Подтвердить", exact: true })
      .click();
    expect((await stale).status()).toBe(409);
    await expect(panel.getByRole("button", { name: "Заблокировать", exact: true })).toBeEnabled();
    expect(writes).toBe(2);
    expect(await readLot()).toMatchObject({
      base_units: "80",
      closed_packs: "4",
      opened_units: "0",
      is_blocked: false,
    });
    // A fresh explicit decision is allowed after reviewing the current state.
    await panel.getByRole("button", { name: "Заблокировать", exact: true }).click();
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "Подтвердить", exact: true })
      .click();
    await expect(panel.getByText("Партия заблокирована.", { exact: true })).toBeVisible();
    expect(writes).toBe(3);
    expect(await readLot()).toMatchObject({
      base_units: "80",
      closed_packs: "4",
      opened_units: "0",
      is_blocked: true,
    });
  } finally {
    await api.dispose();
    await anonymous.dispose();
  }
});
