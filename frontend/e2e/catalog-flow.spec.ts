import { test, request, type Page } from "@playwright/test";

import {
  apiContext,
  apiLogin,
  catalogSearchKey,
  clearLoginRateLimit,
  expect,
  loginInBrowser,
  OWNER,
  selectActionMenuItem,
  uniqueName,
} from "./helpers";

function catalogItem(page: Page, name: string) {
  return page.locator('section[aria-label="Товары каталога"] article').filter({ hasText: name });
}

test.describe("Catalog flow (owner)", () => {
  test.beforeEach(() => {
    clearLoginRateLimit(OWNER.email);
  });

  test("create a catalog item via the UI and see it in the table", async ({ page }) => {
    await loginInBrowser(page, OWNER);
    await page.goto("/catalog");

    const name = uniqueName("E2E Item");
    await page.getByRole("button", { name: /Добавить товар/ }).click();
    const dialog = page.getByRole("dialog", { name: "Добавить товар" });
    await dialog.getByLabel("Торговое название").fill(name);
    await dialog.getByLabel("Условия отпуска", { exact: true }).selectOption("otc");
    await dialog.getByLabel("Условия хранения", { exact: true }).selectOption("normal");
    await dialog.getByLabel(/Цена по умолчанию/).fill("12.50");
    await dialog.getByRole("button", { name: /^Добавить товар$/ }).click();
    await expect(dialog).toBeHidden({ timeout: 15_000 });

    // Modal closes and the row appears. Catalog page uses a trigram search;
    // search by the unique name tail so accumulated same-prefix rows don't
    // push the new item off page 1 (the shared test DB carries many).
    await page.getByLabel(/Поиск/).fill(catalogSearchKey(name));
    await expect(catalogItem(page, name)).toBeVisible({
      timeout: 15_000,
    });
  });

  test("add a barcode to an existing item and see it persist", async ({ page }) => {
    // Seed an item via the API so the test does not depend on test #1.
    const apiAnon = await request.newContext();
    const tokens = await apiLogin(apiAnon, OWNER);
    const api = await apiContext(tokens.access_token);
    const name = uniqueName("E2E Barcode");
    const createRes = await api.post("catalog", {
      data: { brand_name: name, dispensing_type: "otc", storage_type: "normal" },
    });
    if (!createRes.ok()) {
      throw new Error(`POST catalog → ${createRes.status()} ${await createRes.text()}`);
    }
    await apiAnon.dispose();
    await api.dispose();

    await loginInBrowser(page, OWNER);
    await page.goto("/catalog");
    // Search by the unique tail (not the shared "E2E Barcode-" prefix) so the
    // new item is the only trigram match and lands on page 1.
    await page.getByLabel(/Поиск/).fill(catalogSearchKey(name));
    await expect(catalogItem(page, name)).toBeVisible({
      timeout: 15_000,
    });

    // Open the edit modal — BarcodesPanel is rendered next to the form.
    await selectActionMenuItem(page, `Действия для ${name}`, "Изменить");

    const dialog = page.locator('div[role="dialog"]');
    const code = `123456${Date.now().toString().slice(-7)}`.slice(0, 13);
    const addBarcodeResponse = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        response.url().includes("/api/v1/catalog/") &&
        response.url().endsWith("/barcodes") &&
        response.ok(),
    );
    await dialog.getByLabel("Код", { exact: true }).fill(code);
    await dialog.getByRole("button", { name: "+ Добавить", exact: true }).click();

    // Newly added barcode shows up in the per-item list.
    await addBarcodeResponse;
    await expect(dialog.getByText(code)).toBeVisible({ timeout: 30_000 });
  });

  test("trigram search filters the table after the debounce window", async ({ page }) => {
    // Seed a distinctive item via the API.
    const apiAnon = await request.newContext();
    const tokens = await apiLogin(apiAnon, OWNER);
    const api = await apiContext(tokens.access_token);
    const needle = uniqueName("Zzunique");
    const createRes = await api.post("catalog", {
      data: { brand_name: needle, dispensing_type: "otc", storage_type: "normal" },
    });
    expect(createRes.ok()).toBeTruthy();
    await apiAnon.dispose();
    await api.dispose();

    await loginInBrowser(page, OWNER);
    await page.goto("/catalog");

    // Search the unique tail (debounce is 300ms; assertion waits). Using the
    // distinctive suffix keeps the trigram result to this one row regardless of
    // how many similar test items the shared DB has accumulated.
    await page.getByLabel(/Поиск/).fill(catalogSearchKey(needle));
    await expect(catalogItem(page, needle)).toBeVisible({
      timeout: 15_000,
    });
  });

  test("upload and remove an optional product photo", async ({ page }) => {
    await page.setViewportSize({ width: 1600, height: 900 });
    const apiAnon = await request.newContext();
    const tokens = await apiLogin(apiAnon, OWNER);
    const api = await apiContext(tokens.access_token);
    const name = uniqueName("E2E Photo");
    const createRes = await api.post("catalog", {
      data: { brand_name: name, dispensing_type: "otc", storage_type: "normal" },
    });
    expect(createRes.ok()).toBeTruthy();
    await apiAnon.dispose();
    await api.dispose();

    await loginInBrowser(page, OWNER);
    await page.goto("/catalog");
    await page.getByLabel(/Поиск/).fill(catalogSearchKey(name));

    const item = catalogItem(page, name);
    await expect(item).toBeVisible({ timeout: 15_000 });
    await item.locator("button").first().click();

    const details = page.locator('aside[aria-label="Карточка выбранной позиции"]');
    await expect(details).toBeVisible();

    const uploadResponse = page.waitForResponse(
      (response) =>
        response.request().method() === "PUT" &&
        /\/api\/v1\/catalog\/[^/]+\/image$/.test(response.url()) &&
        response.ok(),
    );
    await details
      .getByLabel("Выбрать фотографию товара")
      .setInputFiles("public/icons/icon-192.png");
    await uploadResponse;

    await expect(details.getByText("Фотография товара", { exact: true })).toBeVisible();
    await expect(details.getByRole("img", { name: `Упаковка ${name}` })).toBeVisible({
      timeout: 15_000,
    });

    await details.getByRole("button", { name: "Удалить", exact: true }).click();
    const deleteResponse = page.waitForResponse(
      (response) =>
        response.request().method() === "DELETE" &&
        /\/api\/v1\/catalog\/[^/]+\/image$/.test(response.url()) &&
        response.ok(),
    );
    await page.getByRole("dialog").getByRole("button", { name: "Удалить фото" }).click();
    await deleteResponse;
    await expect(details.getByText("Фото не добавлено", { exact: true })).toBeVisible();
  });
});
