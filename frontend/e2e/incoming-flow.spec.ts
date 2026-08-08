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
    const supplierPicker = dialog.getByRole("combobox", { name: "Поставщик" });
    await supplierPicker.fill(supplier.name);
    const supplierOption = dialog.getByRole("option", { name: supplier.name });
    await expect(supplierOption).toBeVisible({ timeout: 15_000 });
    await supplierOption.click();
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
    const batchCatalogPicker = page.getByRole("combobox", { name: "Товар" });
    await batchCatalogPicker.fill(searchKey);
    const batchCatalogOption = page.getByRole("option", {
      name: new RegExp(item.brand_name),
    });
    await expect(batchCatalogOption).toBeVisible({ timeout: 15_000 });
    await batchCatalogOption.click();

    const batchCard = page.getByRole("article", {
      name: new RegExp(`${item.brand_name}, партия без номера`),
    });
    await expect(batchCard).toBeVisible({ timeout: 15_000 });
    await expect(batchCard).toContainText(branch.name);
    await expect(batchCard).toContainText("10");

    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1,
      ),
    ).toBe(true);
    const openBatchButton = batchCard.getByRole("button", {
      name: new RegExp(`Открыть партию без номера товара ${item.brand_name}`),
    });
    const openBatchBounds = await openBatchButton.boundingBox();
    expect(openBatchBounds).not.toBeNull();
    expect(openBatchBounds!.height).toBeGreaterThanOrEqual(44);
    await openBatchButton.click();

    const batchDialog = page.getByRole("dialog", { name: "Карточка партии" });
    await expect(batchDialog.getByRole("heading", { name: item.brand_name })).toBeVisible();
    await batchDialog.getByRole("button", { name: "Списать" }).click();
    const writeOffDialog = page.getByRole("dialog", { name: "Списание партии" });
    await writeOffDialog.getByLabel("Количество").fill("1");
    await writeOffDialog.getByLabel("Причина").selectOption("damaged");
    await writeOffDialog.getByLabel("Комментарий").fill("E2E: повреждена упаковка");
    await writeOffDialog.getByRole("button", { name: "Подтвердить списание" }).click();

    await expect(writeOffDialog).toBeHidden({ timeout: 15_000 });
    const writeOffMovement = batchDialog.getByRole("article").filter({ hasText: "Акт списания" });
    await expect(writeOffMovement).toContainText("Списание", { timeout: 15_000 });
    await expect(writeOffMovement).toContainText("-1");
    await expect(batchDialog.getByText("9", { exact: true }).first()).toBeVisible();

    // ---- UI: supplier card → source-bound return → stock decreases again ----
    await page.goto("/suppliers");
    await page.getByLabel("Поиск").fill(supplier.name);
    const supplierCard = page.getByRole("article").filter({ hasText: supplier.name });
    await expect(supplierCard).toBeVisible({ timeout: 15_000 });
    await supplierCard.getByRole("button", { name: "Открыть карточку" }).click();

    const supplierDialog = page.getByRole("dialog", { name: supplier.name });
    await supplierDialog.getByRole("button", { name: "Оформить возврат" }).click();
    const returnDialog = page.getByRole("dialog", {
      name: new RegExp(`Возврат: ${supplier.name}`),
    });
    const returnCandidate = returnDialog.getByRole("option", {
      name: new RegExp(item.brand_name),
    });
    await expect(returnCandidate).toBeVisible({ timeout: 15_000 });
    await returnCandidate.click();
    await returnDialog.getByLabel("Количество").fill("1");
    await returnDialog.getByLabel("Причина").selectOption("incorrect_delivery");
    await returnDialog.getByLabel("Комментарий").fill("E2E: ошибка поставки");
    await returnDialog.getByRole("button", { name: "Оформить возврат" }).click();

    await expect(returnDialog).toBeHidden({ timeout: 15_000 });
    const returnEntry = supplierDialog.getByRole("article").filter({ hasText: item.brand_name });
    await expect(returnEntry).toContainText("Ошибка поставки", { timeout: 15_000 });
    await expect(returnEntry).toContainText("4,00 TJS");

    await page.goto("/batches");
    const refreshedBatchPicker = page.getByRole("combobox", { name: "Товар" });
    await refreshedBatchPicker.fill(searchKey);
    const refreshedItemOption = page.getByRole("option", {
      name: new RegExp(item.brand_name),
    });
    await expect(refreshedItemOption).toBeVisible({ timeout: 15_000 });
    await refreshedItemOption.click();
    const refreshedBatchCard = page.getByRole("article", {
      name: new RegExp(`${item.brand_name}, партия без номера`),
    });
    await expect(refreshedBatchCard).toContainText("8", { timeout: 15_000 });
  });
});

function isoDateInDays(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
}
