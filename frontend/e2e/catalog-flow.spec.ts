import { test, request } from "@playwright/test";

import {
  apiContext,
  apiLogin,
  clearLoginRateLimit,
  expect,
  loginInBrowser,
  OWNER,
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

    // Modal closes and the row appears. Catalog page uses a trigram search
    // so we type the new name into the filter to scope the table.
    await page.getByLabel(/Поиск/).fill(name);
    await expect(page.getByRole("cell", { name })).toBeVisible({ timeout: 15_000 });
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
    await page.getByLabel(/Поиск/).fill(name);
    await expect(page.getByRole("cell", { name })).toBeVisible({ timeout: 15_000 });

    // Open the edit modal — BarcodesPanel is rendered next to the form.
    await page
      .getByRole("row", { name: new RegExp(name) })
      .getByRole("button", { name: /Изменить/ })
      .click();

    const dialog = page.locator('div[role="dialog"]');
    const code = `123456${Date.now().toString().slice(-7)}`.slice(0, 13);
    await dialog.getByLabel("Код", { exact: true }).fill(code);
    await dialog.getByRole("button", { name: /\+ Добавить/ }).click();

    // Newly added barcode shows up in the per-item list.
    await expect(dialog.getByText(code)).toBeVisible();
  });

  test("trigram search filters the table after the debounce window", async ({ page }) => {
    // Seed a distinctive item via the API.
    const apiAnon = await request.newContext();
    const tokens = await apiLogin(apiAnon, OWNER);
    const api = await apiContext(tokens.access_token);
    const needle = `Zzunique-${Date.now().toString(36)}`;
    const createRes = await api.post("catalog", {
      data: { brand_name: needle, dispensing_type: "otc", storage_type: "normal" },
    });
    expect(createRes.ok()).toBeTruthy();
    await apiAnon.dispose();
    await api.dispose();

    await loginInBrowser(page, OWNER);
    await page.goto("/catalog");

    // Type three chars from the needle — debounce is 300ms; assertion waits.
    await page.getByLabel(/Поиск/).fill(needle.slice(0, 6));
    await expect(page.getByRole("cell", { name: needle })).toBeVisible({ timeout: 15_000 });
  });
});
