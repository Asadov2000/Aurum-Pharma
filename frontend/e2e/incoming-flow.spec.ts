import { test, request } from "@playwright/test";

import {
  apiContext,
  catalogSearchKey,
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

test.describe("Incoming flow (owner)", () => {
  test.beforeEach(() => {
    clearLoginRateLimit(OWNER.email);
  });

  test("owner creates a draft, adds an item, accepts, and a batch appears in /batches", async ({
    page,
  }) => {
    // ---- API seed: branch + supplier + catalog item ----
    const apiAnon = await request.newContext();
    const tokens = await apiLogin(apiAnon, OWNER);
    const api = await apiContext(tokens.access_token);
    const branch = await seedBranch(api, uniqueName("E2E Branch"));
    const supplier = await seedSupplier(api, uniqueName("E2E Supplier"));
    const item = await seedCatalogItem(api, uniqueName("E2E Med"), "5.00");
    await apiAnon.dispose();
    await api.dispose();

    // ---- UI: create a draft incoming document ----
    await loginInBrowser(page, OWNER);
    await page.goto("/incoming");
    await page.getByRole("button", { name: /\+ Новый приход/ }).click();

    const dialog = page.locator('div[role="dialog"]');
    await dialog.getByLabel("Точка").selectOption({ label: branch.name });
    await dialog.getByLabel("Поставщик").selectOption({ label: supplier.name });
    const docNumber = `E2E-${Date.now()}`;
    await dialog.getByLabel("Номер", { exact: true }).fill(docNumber);
    await dialog.getByRole("button", { name: /Создать черновик/ }).click();

    // We land on the detail page after creation.
    await expect(page).toHaveURL(/\/incoming\/[0-9a-f-]+$/);

    // ---- UI: open the add-item form, then pick a catalog row ----
    await page.getByRole("button", { name: /\+ Добавить позицию/ }).click();
    const pickerInput = page.getByPlaceholder(/Начните вводить название/);
    // Use a search string with enough characters to be unique. CatalogPicker
    // debounces at 200ms, then fires the trigram search.
    const searchKey = catalogSearchKey(item.brand_name);
    await pickerInput.fill(searchKey);
    const option = page.getByRole("option", { name: new RegExp(item.brand_name) });
    await expect(option).toBeVisible({ timeout: 15_000 });
    await option.click();

    const expiresAt = isoDateInDays(180);
    await page.getByLabel("Срок годности").fill(expiresAt);
    await page.getByLabel("Количество").fill("10");
    // Disambiguate between "Цена закупки" and "Цена продажи" — both are
    // exact labels, not regex.
    await page.getByLabel("Цена закупки").fill("4.00");
    await page.getByLabel("Цена продажи").fill("5.00");
    // The form's submit button is plain «Добавить». The page header carries
    // «+ Добавить позицию», so we anchor to the exact label here.
    await page.getByRole("button", { name: "Добавить", exact: true }).click();

    // The new row appears in the items table.
    await expect(page.getByText("10", { exact: false }).first()).toBeVisible();

    // ---- UI: accept → batch lands on /batches ----
    await page.getByRole("button", { name: /Принять/ }).click();
    const acceptDialog = page.getByRole("dialog").filter({ hasText: /Принять приход/ });
    await expect(acceptDialog).toBeVisible();
    await acceptDialog.getByRole("button", { name: /^Принять$/ }).click();

    // Status badge flips to "Принят" (or similar accepted-label).
    await expect(page.getByText(/Принят/)).toBeVisible({ timeout: 15_000 });

    // Hop over to /batches and filter by the unique catalog item → see the
    // freshly-made batch. Branch options are loaded separately and can lag
    // under full-suite load; the catalog picker searches the exact seeded item.
    await page.goto("/batches");
    const batchCatalogPicker = page.getByPlaceholder("Найти по названию…");
    await batchCatalogPicker.fill(searchKey);
    const batchCatalogOption = page.getByRole("option", {
      name: new RegExp(item.brand_name),
    });
    await expect(batchCatalogOption).toBeVisible({ timeout: 15_000 });
    await batchCatalogOption.click();

    // The brand_name is not on the batch row directly (UI shows batch_number),
    // so assert the visible table row for the created branch and its 10 / 10 stock.
    const batchRow = page
      .getByRole("row")
      .filter({ hasText: branch.name })
      .filter({ hasText: /10(?:\.0+)?\s*\/\s*10(?:\.0+)?/ });
    await expect(batchRow.first()).toBeVisible({ timeout: 15_000 });
  });
});

function isoDateInDays(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
}
