import { test, request } from "@playwright/test";

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

test.describe("Catalog flow (owner)", () => {
  test.beforeEach(() => {
    clearLoginRateLimit(OWNER.email);
  });

  test("create a catalog item via the UI and see it in the table", async ({ page }) => {
    await loginInBrowser(page, OWNER);
    await page.goto("/catalog");

    const name = uniqueName("E2E Item");
    await page.getByRole("button", { name: /\+ Новая позиция/ }).click();
    await page.getByLabel("Торговое название").fill(name);
    await page.getByLabel(/Базовая цена/).fill("12.50");
    await page.getByRole("button", { name: /^Создать$/ }).click();
    await expect(page.getByRole("dialog")).toBeHidden({ timeout: 15_000 });

    // Modal closes and the row appears. Catalog page uses a trigram search;
    // search by the unique name tail so accumulated same-prefix rows don't
    // push the new item off page 1 (the shared test DB carries many).
    await page.getByLabel(/Поиск/).fill(catalogSearchKey(name));
    await expect(page.getByRole("cell", { name, exact: true })).toBeVisible({
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
    await expect(page.getByRole("cell", { name, exact: true })).toBeVisible({
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
    await dialog.getByRole("button", { name: /\+ Добавить/ }).click();

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
    await expect(page.getByRole("cell", { name: needle, exact: true })).toBeVisible({
      timeout: 15_000,
    });
  });
});
