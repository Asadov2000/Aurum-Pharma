import { request, test } from "@playwright/test";

import {
  apiContext,
  apiLogin,
  catalogSearchKey,
  clearLoginRateLimit,
  expect,
  loginInBrowser,
  OWNER,
  seedAcceptedBatch,
  seedBranch,
  seedCatalogItem,
  seedRegister,
  seedSupplier,
  uniqueName,
} from "./helpers";

test("receipt menus preserve completed sales and persist independent preferences", async ({
  page,
}) => {
  clearLoginRateLimit(OWNER.email);
  const anonymous = await request.newContext();
  const tokens = await apiLogin(anonymous, OWNER);
  const api = await apiContext(tokens.access_token);
  const branch = await seedBranch(api, uniqueName("Receipt context"));
  const register = await seedRegister(api, branch.id, uniqueName("Receipt register"));
  const supplier = await seedSupplier(api, uniqueName("Receipt supplier"));
  const item = await seedCatalogItem(api, uniqueName("Receipt product"), "10.00");
  await seedAcceptedBatch(api, {
    branchId: branch.id,
    supplierId: supplier.id,
    catalogId: item.id,
    qty: "2",
    purchasePrice: "5.00",
    salePrice: "10.00",
    expiresAt: "2028-10-03",
    batchNumber: uniqueName("Receipt batch"),
  });
  const shift = await api.post("shifts/open", {
    data: { register_id: register.id, opening_cash: "0" },
  });
  expect(shift.ok()).toBeTruthy();
  const checkout = await api.post("sales/checkout", {
    data: {
      operation_id: crypto.randomUUID(),
      register_id: register.id,
      items: [{ catalog_id: item.id, qty: "1" }],
      payments: [{ payment_method: "cash", amount: "10.00" }],
    },
  });
  expect(checkout.ok()).toBeTruthy();
  const completed = (await checkout.json()) as { sale_id: string; receipt_number: string };
  await api.dispose();
  await anonymous.dispose();

  const saleWrites: string[] = [];
  page.on("request", (req) => {
    if (req.method() !== "GET" && new URL(req.url()).pathname.startsWith("/api/v1/sales"))
      saleWrites.push(req.method());
  });
  await loginInBrowser(page, OWNER);
  await page.goto("/sales");
  await page.context().grantPermissions(["clipboard-read", "clipboard-write"]);
  const filter = () =>
    page.waitForResponse((response) => {
      const url = new URL(response.url());
      return (
        response.request().method() === "GET" &&
        url.pathname === "/api/v1/sales" &&
        url.searchParams.get("receipt_number") === completed.receipt_number &&
        response.ok()
      );
    });
  const filtered = filter();
  await page.getByLabel("№ чека").fill(completed.receipt_number);
  await filtered;
  const row = page
    .locator("article[data-context-actions]")
    .filter({ hasText: completed.receipt_number })
    .filter({ hasText: register.name });
  await expect(row).toBeVisible();
  await row.click({ button: "right" });
  const menu = page.getByRole("menu");
  await expect(menu.getByText(new RegExp(register.name))).toBeVisible();
  await expect(menu.getByRole("menuitem", { name: "Скопировать номер чека" })).toBeVisible();
  await expect(menu.getByRole("menuitem", { name: /возврат|Удалить|Печать/i })).toHaveCount(0);
  await menu.getByRole("menuitem", { name: "Скопировать номер чека" }).click();
  await expect
    .poll(() => page.evaluate(() => navigator.clipboard.readText()))
    .toBe(completed.receipt_number);
  await row.click({ button: "right" });
  await menu.getByRole("menuitem", { name: "Открыть чек", exact: true }).click();
  const details = page.getByRole("dialog", {
    name: `Чек № ${completed.receipt_number}`,
    exact: true,
  });
  await expect(details).toBeVisible();
  await expect(details.getByText(item.brand_name, { exact: true })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(details).toHaveCount(0);
  await expect(row).toBeFocused();

  await row.focus();
  await page.keyboard.press("Shift+F10");
  await menu.getByRole("menuitem", { name: "Настроить это меню" }).click();
  await expect(page.getByRole("button", { name: "Чеки и возвраты", exact: true })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await page.getByRole("checkbox", { name: "Показывать в меню: Скопировать номер чека" }).uncheck();
  const enabled = page.getByRole("checkbox", { name: "Меню по правой кнопке" });
  await enabled.uncheck();
  const saved = page.waitForResponse(
    (response) =>
      response.request().method() === "PATCH" &&
      new URL(response.url()).pathname === "/api/v1/auth/preferences" &&
      response.ok(),
  );
  await page.getByRole("button", { name: "Сохранить настройки", exact: true }).click();
  await saved;
  await page.reload();
  await expect(enabled).not.toBeChecked();
  await expect(
    page.getByRole("checkbox", { name: "Показывать в меню: Скопировать номер чека" }),
  ).not.toBeChecked();
  await page.goto("/sales");
  const filteredAgain = filter();
  await page.getByLabel("№ чека").fill(completed.receipt_number);
  await filteredAgain;
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(row).toBeVisible();
  await row.click({ button: "right" });
  await expect(menu).toHaveCount(0);
  await row
    .getByRole("button", { name: `Действия для чека № ${completed.receipt_number}` })
    .click();
  await expect(menu.getByRole("menuitem", { name: "Открыть чек", exact: true })).toBeVisible();
  await expect(menu.getByRole("menuitem", { name: "Скопировать номер чека" })).toHaveCount(0);
  expect(saleWrites).toEqual([]);
  await menu.getByRole("menuitem", { name: "Настроить это меню" }).click();
  await page.getByRole("button", { name: "Вернуть стандартные настройки" }).click();
  const restored = page.waitForResponse(
    (response) =>
      response.request().method() === "PATCH" &&
      new URL(response.url()).pathname === "/api/v1/auth/preferences" &&
      response.ok(),
  );
  await page.getByRole("button", { name: "Сохранить настройки", exact: true }).click();
  await restored;
});

test("catalog right-click and keyboard actions respect personal settings after reload", async ({
  page,
}) => {
  clearLoginRateLimit(OWNER.email);
  const anonymous = await request.newContext();
  const tokens = await apiLogin(anonymous, OWNER);
  const api = await apiContext(tokens.access_token);
  const name = uniqueName("Context menu");
  const created = await api.post("catalog", {
    data: { brand_name: name, dispensing_type: "otc", storage_type: "normal" },
  });
  expect(created.ok()).toBeTruthy();
  await api.dispose();
  await anonymous.dispose();

  await loginInBrowser(page, OWNER);
  await page.goto("/catalog");
  const waitForSearch = () =>
    page.waitForResponse((response) => {
      const url = new URL(response.url());
      return (
        response.request().method() === "GET" &&
        url.pathname === "/api/v1/catalog" &&
        url.searchParams.get("q") === catalogSearchKey(name) &&
        response.ok()
      );
    });
  const filtered = waitForSearch();
  await page.getByLabel(/Поиск/).fill(catalogSearchKey(name));
  await filtered;
  const row = page
    .locator('section[aria-label="Товары каталога"] article')
    .filter({ hasText: name });
  await expect(row).toBeVisible();
  await expect(page.getByText(/^(Поиск…|Обновление…)$/)).toHaveCount(0);
  await row.click({ button: "right" });
  const menu = page.getByRole("menu");
  await expect(menu.getByText(name, { exact: true })).toBeVisible();
  await expect(menu.getByRole("menuitem", { name: "Изменить" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(row).toBeFocused();
  await page.keyboard.press("Shift+F10");
  await expect(menu).toBeVisible();
  await menu.getByRole("menuitem", { name: "Настроить это меню" }).click();
  await expect(page.getByRole("heading", { name: "Быстрые действия" })).toBeVisible();
  const checkbox = page.getByRole("checkbox", { name: "Меню по правой кнопке" });
  await checkbox.uncheck();
  const saved = page.waitForResponse(
    (response) =>
      response.request().method() === "PATCH" &&
      new URL(response.url()).pathname === "/api/v1/auth/preferences" &&
      response.ok(),
  );
  await page.getByRole("button", { name: "Сохранить настройки", exact: true }).click();
  await saved;
  await page.reload();
  await expect(checkbox).not.toBeChecked();
  await page.goto("/catalog");
  const filteredAgain = waitForSearch();
  await page.getByLabel(/Поиск/).fill(catalogSearchKey(name));
  await filteredAgain;
  await expect(row).toBeVisible();
  await expect(page.getByText(/^(Поиск…|Обновление…)$/)).toHaveCount(0);
  await row.click({ button: "right" });
  await expect(page.getByRole("menu")).toHaveCount(0);
  await row.getByRole("button", { name: `Действия для ${name}` }).click();
  await expect(menu).toBeVisible();
  await menu.getByRole("menuitem", { name: "Настроить это меню" }).click();
  await checkbox.check();
  const restored = page.waitForResponse(
    (response) =>
      response.request().method() === "PATCH" &&
      new URL(response.url()).pathname === "/api/v1/auth/preferences" &&
      response.ok(),
  );
  await page.getByRole("button", { name: "Сохранить настройки", exact: true }).click();
  await restored;
});

test("receiving and employee menus open their own settings and preserve read-only navigation", async ({
  page,
}) => {
  clearLoginRateLimit(OWNER.email);
  const anonymous = await request.newContext();
  const tokens = await apiLogin(anonymous, OWNER);
  const api = await apiContext(tokens.access_token);
  const branch = await seedBranch(api, uniqueName("Context branch"));
  const supplier = await seedSupplier(api, uniqueName("Context supplier"));
  const number = uniqueName("Context invoice");
  const created = await api.post("incoming", {
    data: {
      operation_id: crypto.randomUUID(),
      branch_id: branch.id,
      supplier_id: supplier.id,
      document_date: "2026-10-03",
      document_number: number,
    },
  });
  expect(created.ok()).toBeTruthy();
  const document = (await created.json()) as { id: string };
  await api.dispose();
  await anonymous.dispose();
  await page.setViewportSize({ width: 1000, height: 800 });
  await loginInBrowser(page, OWNER);
  await page.goto("/incoming");
  const firstFiltered = page.waitForResponse(
    (response) =>
      response.request().method() === "GET" &&
      new URL(response.url()).pathname === "/api/v1/incoming" &&
      new URL(response.url()).searchParams.get("document_number") === number &&
      response.ok(),
  );
  await page.getByLabel("Номер документа", { exact: true }).fill(number);
  await firstFiltered;
  const row = page.locator("tr[data-context-actions]").filter({ hasText: number });
  await expect(row).toBeVisible();
  await expect(row.getByRole("button", { name: `Действия для приёмки ${number}` })).toBeEnabled();
  await row.click({ button: "right" });
  const menu = page.getByRole("menu");
  await expect(menu.getByRole("menuitem", { name: "Скопировать номер документа" })).toBeVisible();
  await expect(menu.getByRole("menuitem", { name: "Принять" })).toHaveCount(0);
  await menu.getByRole("menuitem", { name: "Настроить это меню" }).click();
  await expect(page.getByRole("button", { name: "Приёмка товаров", exact: true })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  const copy = page.getByRole("checkbox", {
    name: "Показывать в меню: Скопировать номер документа",
  });
  await copy.uncheck();
  const saved = page.waitForResponse(
    (response) =>
      response.request().method() === "PATCH" &&
      new URL(response.url()).pathname === "/api/v1/auth/preferences" &&
      response.ok(),
  );
  await page.getByRole("button", { name: "Сохранить настройки", exact: true }).click();
  await saved;
  await page.reload();
  await expect(copy).not.toBeChecked();
  await page.goto("/incoming");
  const secondFiltered = page.waitForResponse(
    (response) =>
      response.request().method() === "GET" &&
      new URL(response.url()).pathname === "/api/v1/incoming" &&
      new URL(response.url()).searchParams.get("document_number") === number &&
      response.ok(),
  );
  await page.getByLabel("Номер документа", { exact: true }).fill(number);
  await secondFiltered;
  await expect(row.getByRole("button", { name: `Действия для приёмки ${number}` })).toBeEnabled();
  await row.click({ button: "right" });
  await expect(menu.getByRole("menuitem", { name: "Скопировать номер документа" })).toHaveCount(0);
  await menu.getByRole("menuitem", { name: "Открыть документ" }).click();
  await expect(page).toHaveURL(new RegExp(`/incoming/${document.id}$`));

  await page.goto("/users");
  const employee = page
    .getByRole("list", { name: "Сотрудники аптеки" })
    .locator("li[data-context-actions]")
    .first();
  await expect(employee).toBeVisible();
  await employee.click({ button: "right" });
  await menu.getByRole("menuitem", { name: "Настроить это меню" }).click();
  await expect(page.getByRole("button", { name: "Сотрудники", exact: true })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await page.goto("/users");
  await employee.click({ button: "right" });
  await menu.getByRole("menuitem", { name: "Открыть карточку" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
});
