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
    await page.addInitScript(() => {
      window.localStorage.setItem("ui:density", "touch");
    });
    await page.setViewportSize({ width: 320, height: 568 });
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

    // Editing also runs in the touch layout. Explicitly clearing an optional
    // value must persist as null instead of silently restoring the old value.
    await page.getByRole("button", { name: "Изменить реквизиты" }).click();
    const documentDialog = page.getByRole("dialog", { name: "Реквизиты прихода" });
    await documentDialog.getByLabel("Номер", { exact: true }).clear();
    await documentDialog.getByRole("button", { name: "Сохранить" }).click();
    await expect(page.getByRole("heading", { name: "Приход без номера" })).toBeVisible();

    // ---- UI: open the add-item form, then pick a catalog row ----
    await page.getByRole("button", { name: "Добавить позицию" }).click();
    const pickerInput = page.getByPlaceholder(/Начните вводить название/);
    // Use a search string with enough characters to be unique. CatalogPicker
    // debounces at 200ms, then fires the trigram search.
    const searchKey = catalogSearchKey(item.brand_name);
    await pickerInput.fill(searchKey);
    const option = page.getByRole("option", { name: new RegExp(item.brand_name) });
    await expect(option).toBeVisible({ timeout: 15_000 });
    await option.click();

    const expiresAt = isoDateInDays(180);
    await page.getByLabel("Номер партии").fill("E2E-BATCH");
    await page.getByLabel("Произведена").fill(isoDateInDays(-30));
    await page.getByLabel("Срок годности").fill(expiresAt);
    await page.getByLabel("Количество").fill("10");
    // Disambiguate between "Цена закупки" and "Цена продажи" — both are
    // exact labels, not regex.
    await page.getByLabel("Цена закупки").fill("4.00");
    await page.getByLabel("Цена продажи").fill("5.00");
    // The form's submit button is plain «Добавить». The page header carries
    // «+ Добавить позицию», so we anchor to the exact label here.
    await page.getByRole("button", { name: "Добавить", exact: true }).click();

    // The enriched response shows the product in one responsive DOM tree.
    const createdItemCard = page.getByRole("article", { name: item.brand_name });
    await expect(createdItemCard).toBeVisible();
    await expect(createdItemCard).toContainText("10");

    // Exercise item editing and clearing nullable fields on the touch form.
    await createdItemCard.getByRole("button", { name: "Изменить" }).click();
    const itemDialog = page.getByRole("dialog", { name: "Изменить позицию" });
    await itemDialog.getByLabel("Номер партии").clear();
    await itemDialog.getByLabel("Произведена").clear();
    await itemDialog.getByLabel("Цена продажи").fill("6.00");
    await itemDialog.getByRole("button", { name: "Сохранить" }).click();
    await expect(createdItemCard).toContainText("Без номера");
    await expect(createdItemCard).toContainText("6,00 TJS");

    // The touch layout keeps the document usable without page-level overflow.
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1,
      ),
    ).toBe(true);
    const addPositionBounds = await page
      .getByRole("button", { name: "Добавить позицию" })
      .boundingBox();
    expect(addPositionBounds).not.toBeNull();
    expect(addPositionBounds!.height).toBeGreaterThanOrEqual(44);

    // ---- UI: accept → batch lands on /batches ----
    await page.getByRole("button", { name: "Принять приход" }).click();
    const acceptDialog = page.getByRole("dialog").filter({ hasText: /Принять приход/ });
    await expect(acceptDialog).toBeVisible();
    await acceptDialog.getByRole("button", { name: "Принять приход" }).click();

    // Wait for the exact accepted status. A partial match also finds the
    // "Принять" action before its request has completed.
    await expect(page.getByText("Принят", { exact: true })).toBeVisible({ timeout: 15_000 });

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
