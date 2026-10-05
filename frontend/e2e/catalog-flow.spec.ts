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
    await dialog.getByLabel("Название лекарства").fill(name);
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
    const searchKey = catalogSearchKey(name);
    const filteredResponse = page.waitForResponse((response) => {
      const url = new URL(response.url());
      return (
        response.request().method() === "GET" &&
        url.pathname === "/api/v1/catalog" &&
        url.searchParams.get("q") === searchKey &&
        response.status() === 200
      );
    });
    await page.getByLabel(/Поиск/).fill(searchKey);
    await filteredResponse;
    await expect(page.getByText(/^(Поиск…|Обновление…)$/)).toHaveCount(0);
    await expect(catalogItem(page, name)).toBeVisible({
      timeout: 15_000,
    });

    // Open the edit modal — BarcodesPanel is rendered next to the form.
    await page
      .getByRole("button", { name: `Действия для ${name}`, exact: true })
      .scrollIntoViewIfNeeded();
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

  test("prepares unit dispensing separately, reloads it and removes it without changing pack price", async ({
    page,
  }) => {
    const apiAnon = await request.newContext();
    const tokens = await apiLogin(apiAnon, OWNER);
    const api = await apiContext(tokens.access_token);
    const name = uniqueName("E2E Unit Preparation");
    try {
      const created = await api.post("catalog", {
        data: {
          brand_name: name,
          dispensing_type: "otc",
          storage_type: "normal",
          base_price: "30.00",
        },
      });
      expect(created.ok()).toBeTruthy();
      const item = (await created.json()) as { id: string };
      await loginInBrowser(page, OWNER);
      await page.goto("/catalog");
      await page.getByLabel(/Поиск/).fill(catalogSearchKey(name));
      await expect(catalogItem(page, name)).toBeVisible({ timeout: 15_000 });
      await catalogItem(page, name)
        .getByRole("button", { name: `Открыть карточку ${name}` })
        .click();
      await page
        .getByRole("dialog", { name: "Карточка товара" })
        .getByRole("button", { name: "Изменить позицию" })
        .click();
      const editor = page.getByRole("dialog", { name: `Изменить товар: ${name}` });
      const open = editor.getByRole("button", { name: "Подготовить поштучный отпуск" });
      await editor.getByLabel("Название лекарства").fill(`${name} edited`);
      await expect(open).toBeDisabled();
      await editor.getByLabel("Название лекарства").fill(name);
      await open.click();
      const preparation = page.getByRole("dialog", { name: "Подготовка поштучного отпуска" });
      await expect(preparation.getByRole("status")).toContainText(
        "Продажа поштучно ещё недоступна",
      );
      await preparation.getByLabel("Сколько штук в упаковке?").fill("20");
      await preparation.getByText("В пачке есть блистеры (плажки)", { exact: true }).click();
      await preparation.getByLabel("Сколько штук в одном блистере?").fill("3");
      await preparation.getByLabel(/Цена одной единицы/).fill("1,50");
      await preparation.getByRole("button", { name: "Сохранить подготовку" }).click();
      await expect(preparation.getByRole("alert")).toContainText("целое число блистеров");
      await preparation.getByLabel("Сколько штук в одном блистере?").fill("10");
      await preparation.getByRole("button", { name: "Сохранить подготовку" }).click();
      await expect(preparation).toBeHidden();
      await expect(editor).toBeVisible();
      const savedResponse = await api.get(`catalog/${item.id}`);
      expect(savedResponse.ok()).toBeTruthy();
      const saved = (await savedResponse.json()) as {
        base_price: string;
        unit_sale_preparation: unknown;
      };
      expect(saved.base_price).toBe("30.00");
      expect(saved.unit_sale_preparation).toEqual({
        version: 1,
        state: "prepared",
        unit: "tablet",
        units_per_pack: 20,
        dispense_step: 1,
        units_per_blister: 10,
        unit_price: "1.50",
      });
      await open.click();
      await expect(preparation.getByLabel("Сколько штук в упаковке?")).toHaveValue("20");
      await expect(preparation.getByLabel("Сколько штук в одном блистере?")).toHaveValue("10");
      await expect(preparation.getByLabel(/Цена одной единицы/)).toHaveValue("1.50");
      await preparation.getByRole("button", { name: "Убрать подготовку" }).click();
      await expect(preparation).toBeHidden();
      const clearedResponse = await api.get(`catalog/${item.id}`);
      const cleared = (await clearedResponse.json()) as {
        base_price: string;
        unit_sale_preparation: unknown;
      };
      expect(clearedResponse.ok()).toBeTruthy();
      expect(cleared.unit_sale_preparation).toBeNull();
      expect(cleared.base_price).toBe("30.00");
    } finally {
      await apiAnon.dispose();
      await api.dispose();
    }
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
