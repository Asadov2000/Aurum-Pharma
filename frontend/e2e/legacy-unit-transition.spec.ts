import { request, test } from "@playwright/test";

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

test("closed-pack transfer recovers a committed result without a second stock command", async ({
  page,
}) => {
  test.setTimeout(120_000);
  clearLoginRateLimit(OWNER.email);
  const anonymous = await request.newContext();
  const tokens = await apiLogin(anonymous, OWNER);
  const api = await apiContext(tokens.access_token);
  try {
    const branch = await seedBranch(api, uniqueName("Legacy transfer branch"));
    const supplier = await seedSupplier(api, uniqueName("Legacy transfer supplier"));
    const product = await seedCatalogItem(api, uniqueName("Legacy transfer medicine"), "10.00");
    const expiry = new Date();
    expiry.setUTCDate(expiry.getUTCDate() + 180);
    const batchNumber = uniqueName("Transfer");
    const batch = await seedAcceptedBatch(api, {
      branchId: branch.id,
      supplierId: supplier.id,
      catalogId: product.id,
      qty: "3",
      purchasePrice: "5.00",
      salePrice: "10.00",
      expiresAt: expiry.toISOString().slice(0, 10),
      batchNumber,
    });
    await page.setViewportSize({ width: 1100, height: 850 });
    await loginInBrowser(page, OWNER);
    await page.goto("/batches");
    await page.getByLabel("Номер партии", { exact: true }).fill(batchNumber);
    await page
      .getByRole("button", {
        name: `Открыть партию ${batchNumber} товара ${product.brand_name}`,
        exact: true,
      })
      .click();
    const panel = page.getByRole("region", { name: "Перенос в поштучный учёт", exact: true });
    await panel.getByLabel("Единица", { exact: true }).selectOption("tablet");
    await panel.getByLabel("Единиц в упаковке", { exact: true }).fill("20");
    await panel.getByLabel("Единиц в блистере (необязательно)", { exact: true }).fill("10");
    await panel.getByLabel("Шаг отпуска", { exact: true }).fill("1");
    await panel.getByLabel("Закрытых упаковок", { exact: true }).fill("3");
    await panel.getByLabel("Подтверждаю: открытых упаковок нет", { exact: true }).check();
    await panel.getByRole("button", { name: "Проверить пересчёт", exact: true }).click();
    await expect(
      panel.getByText("Количество совпадает. Это ещё не перенос остатков.", { exact: true }),
    ).toBeVisible();
    await panel.getByRole("button", { name: "Сохранить пересчёт", exact: true }).click();
    await expect(
      panel.getByText("Пересчёт сохранён: 3 закрытых упаковок, 60 ед.", { exact: true }),
    ).toBeVisible();
    const before = await api.get(`batches/${batch.id}`);
    expect(before.ok()).toBe(true);
    expect(((await before.json()) as { qty_remaining: string }).qty_remaining).toBe("3.000");

    const commands: string[] = [];
    let completed: { command_id: string; lot_id: string } | undefined;
    await page.route(`**/api/v1/batches/${batch.id}/unit-transition`, async (route) => {
      commands.push((route.request().postDataJSON() as { command_id: string }).command_id);
      const response = await route.fetch();
      expect(response.ok()).toBe(true);
      completed = (await response.json()) as { command_id: string; lot_id: string };
      await route.abort("failed");
    });
    await panel.getByRole("button", { name: "Перенести остаток", exact: true }).click();
    expect(commands).toHaveLength(0);
    await page.getByRole("button", { name: "Подтвердить перенос", exact: true }).click();
    await expect(panel.getByRole("alert")).toBeVisible();
    expect(commands).toHaveLength(1);
    await panel.getByRole("button", { name: "Проверить перенос", exact: true }).click();
    await expect(panel.getByText(/Перенос подтверждён\. Физическая партия:/)).toBeVisible();
    expect(completed?.command_id).toBe(commands[0]);
    expect(commands).toHaveLength(1);
    const after = await api.get(`batches/${batch.id}`);
    expect(after.ok()).toBe(true);
    expect(((await after.json()) as { qty_remaining: string }).qty_remaining).toBe("0.000");
    const lots = await api.get("batches/physical", {
      params: { branch_id: branch.id, show_empty: true },
    });
    expect(lots.ok()).toBe(true);
    const physical = (await lots.json()) as {
      items: { lot_id: string; base_units: string; closed_packs: string }[];
    };
    expect(physical.items).toHaveLength(1);
    expect(physical.items[0]).toMatchObject({
      lot_id: completed?.lot_id,
      closed_packs: "3",
      base_units: "60",
    });
  } finally {
    await api.dispose();
    await anonymous.dispose();
  }
});
