import { request, test } from "@playwright/test";

import {
  addPosItemToCart,
  apiContext,
  apiLogin,
  catalogSearchKey,
  clearLoginRateLimit,
  completePosSale,
  expect,
  loginInBrowser,
  OWNER,
  payPosSaleCash,
  seedAcceptedBatch,
  seedBranch,
  seedCatalogItem,
  seedRegister,
  seedSupplier,
  uniqueName,
} from "./helpers";

test("cart menus target a single FEFO line and become read-only after payment", async ({
  page,
}) => {
  clearLoginRateLimit(OWNER.email);
  await page.setViewportSize({ width: 1366, height: 768 });
  const anonymous = await request.newContext();
  const tokens = await apiLogin(anonymous, OWNER);
  const api = await apiContext(tokens.access_token);
  const branch = await seedBranch(api, uniqueName("Cart context"));
  const register = await seedRegister(api, branch.id, uniqueName("Cart register"));
  const supplier = await seedSupplier(api, uniqueName("Cart supplier"));
  const item = await seedCatalogItem(api, uniqueName("Cart product"), "10.00");
  const batchA = uniqueName("Cart A");
  const batchB = uniqueName("Cart B");
  await seedAcceptedBatch(api, {
    branchId: branch.id,
    supplierId: supplier.id,
    catalogId: item.id,
    qty: "1",
    purchasePrice: "5.00",
    salePrice: "10.00",
    expiresAt: "2028-01-03",
    batchNumber: batchA,
  });
  const secondBatch = await seedAcceptedBatch(api, {
    branchId: branch.id,
    supplierId: supplier.id,
    catalogId: item.id,
    qty: "4",
    purchasePrice: "5.00",
    salePrice: "10.00",
    expiresAt: "2028-10-03",
    batchNumber: batchB,
  });
  await api.dispose();
  await anonymous.dispose();
  await loginInBrowser(page, OWNER);
  await page.context().grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.goto("/pos");
  await page.getByLabel(/^Касса$/).selectOption({ label: register.name });
  await page.getByLabel("Наличные в кассе на начало смены").fill("0");
  await page.getByRole("button", { name: "Открыть смену" }).click();
  await expect(page.getByText("Смена открыта")).toBeVisible();
  await addPosItemToCart(page, { brandName: item.brand_name, qty: "3", expectedCartItems: 2 });
  const first = page.getByTestId("cart-item").filter({ hasText: batchA });
  const second = page.getByTestId("cart-item").filter({ hasText: batchB });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.getByRole("button", { name: "Вид интерфейса", exact: true }).click();
  await page
    .getByRole("dialog", { name: "Вид интерфейса", exact: true })
    .getByRole("button", { name: "Сенсор", exact: true })
    .click();
  await page.keyboard.press("Escape");
  for (const mode of ["Сенсор", "Клавиши"]) {
    await page
      .getByRole("group", { name: "Режим кассы", exact: true })
      .getByRole("button", { name: mode, exact: true })
      .click();
    await expect(second.getByText(batchB, { exact: false })).toBeVisible();
    await expect
      .poll(() => second.evaluate((row) => row.scrollWidth <= row.clientWidth + 1))
      .toBe(true);
    const rowBox = await second.boundingBox();
    const buttonBox = await second.getByRole("button", { name: /^Действия / }).boundingBox();
    expect(rowBox).not.toBeNull();
    expect(buttonBox).not.toBeNull();
    expect(buttonBox!.x + buttonBox!.width).toBeLessThanOrEqual(rowBox!.x + rowBox!.width + 1);
  }
  const paymentWrites: string[] = [];
  page.on("request", (req) => {
    if (
      req.method() === "POST" &&
      /\/api\/v1\/sales\/(checkout|[^/]+\/(payments|complete))$/.test(new URL(req.url()).pathname)
    )
      paymentWrites.push(req.method());
  });
  await second.click({ button: "right" });
  const menu = page.getByRole("menu");
  await expect(menu.getByText(`Партия ${batchB}`, { exact: false })).toBeVisible();
  for (const key of ["F2", "F3", "F4"]) await page.keyboard.press(key);
  await expect(menu).toBeVisible();
  expect(paymentWrites).toEqual([]);
  await menu.getByRole("menuitem", { name: "Скопировать номер партии" }).click();
  await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toBe(batchB);
  await second.click({ button: "right" });
  const increased = page.waitForResponse(
    (response) =>
      response.request().method() === "PATCH" &&
      /\/api\/v1\/sales\/[^/]+\/items\/[^/]+$/.test(new URL(response.url()).pathname) &&
      response.ok(),
  );
  await menu.getByRole("menuitem", { name: "Увеличить количество", exact: true }).click();
  const changed = (await (await increased).json()) as { id: string; batch_id: string; qty: string };
  expect(changed.batch_id).toBe(secondBatch.id);
  expect(Number(changed.qty)).toBe(3);
  await expect(second.getByRole("button", { name: "Количество", exact: true })).toHaveText("3");
  await expect(first.getByRole("button", { name: "Количество", exact: true })).toHaveText("1");
  await second.click({ button: "right" });
  const decreased = page.waitForResponse(
    (response) =>
      response.request().method() === "PATCH" &&
      new URL(response.url()).pathname.endsWith(`/items/${changed.id}`) &&
      response.ok(),
  );
  await menu.getByRole("menuitem", { name: "Уменьшить количество", exact: true }).click();
  expect(Number(((await (await decreased).json()) as { qty: string }).qty)).toBe(2);
  await expect(second.getByRole("button", { name: "Количество", exact: true })).toHaveText("2");
  await first.click({ button: "right" });
  await expect(
    menu.getByRole("menuitem", { name: "Уменьшить количество", exact: true }),
  ).toHaveCount(0);
  const deleted = page.waitForResponse(
    (response) =>
      response.request().method() === "DELETE" &&
      /\/api\/v1\/sales\/[^/]+\/items\/[^/]+$/.test(new URL(response.url()).pathname) &&
      response.ok(),
  );
  await menu.getByRole("menuitem", { name: "Удалить из чека", exact: true }).click();
  await deleted;
  await expect(first).toHaveCount(0);
  await expect(second).toBeVisible();
  await expect(page.getByTestId("cart-item")).toHaveCount(1);
  for (const mode of ["Сенсор", "Клавиши"]) {
    await page
      .getByRole("group", { name: "Режим кассы", exact: true })
      .getByRole("button", { name: mode, exact: true })
      .click();
    await expect(second.getByText(item.brand_name, { exact: true })).toBeVisible();
    await expect(second.getByText(batchB, { exact: false })).toBeVisible();
    await expect
      .poll(() => second.evaluate((row) => row.scrollWidth <= row.clientWidth + 1))
      .toBe(true);
  }
  await payPosSaleCash(page, "20.00");
  await second.click({ button: "right" });
  await expect(menu.getByRole("menuitem", { name: /Увеличить|Уменьшить|Удалить/ })).toHaveCount(0);
  await expect(
    menu.getByRole("menuitem", { name: "Скопировать название", exact: true }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await completePosSale(page);
  await second.getByRole("button", { name: /^Действия / }).click();
  await expect(menu.getByRole("menuitem", { name: /Увеличить|Уменьшить|Удалить/ })).toHaveCount(0);
  await menu.getByRole("menuitem", { name: "Настроить меню" }).click();
  await expect(
    page.getByRole("button", { name: "Касса — товары чека", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
});

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
  await expect(
    row.getByRole("button", { name: `Открыть чек № ${completed.receipt_number}`, exact: true }),
  ).toBeEnabled();
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
  await menu.getByRole("menuitem", { name: "Настроить меню" }).click();
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
  await menu.getByRole("menuitem", { name: "Настроить меню" }).click();
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
}, testInfo) => {
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
  await page.emulateMedia({ reducedMotion: "reduce" });
  for (const scheme of ["light", "dark"] as const) {
    await page.evaluate(
      (theme) => document.documentElement.setAttribute("data-theme", theme),
      scheme,
    );
    for (const width of [1280, 390]) {
      await page.setViewportSize({ width, height: 844 });
      await row.getByRole("button", { name: /^Действия/ }).click();
      await expect(menu).toBeVisible();
      await expect
        .poll(() =>
          menu.evaluate((element) => {
            const channels =
              getComputedStyle(element).backgroundColor.match(/\d+/g)?.slice(0, 3).map(Number) ??
              [];
            return (
              channels.length === 3 && channels.reduce((sum, value) => sum + value, 0) / 3 < 128
            );
          }),
        )
        .toBe(scheme === "dark");
      await expect
        .poll(async () => {
          const bounds = await menu.boundingBox();
          return (
            bounds !== null &&
            bounds.x >= 7 &&
            bounds.y >= 7 &&
            bounds.x + bounds.width <= width - 7 &&
            bounds.y + bounds.height <= 837
          );
        })
        .toBe(true);
      const commands = menu.getByRole("menuitem");
      for (const command of await commands.all()) {
        expect((await command.boundingBox())!.height).toBeGreaterThanOrEqual(39);
      }
      await expect
        .poll(() => menu.evaluate((element) => element.scrollWidth <= element.clientWidth))
        .toBe(true);
      await page.screenshot({ path: testInfo.outputPath(`record-menu-${scheme}-${width}.png`) });
      await page.keyboard.press("Escape");
    }
  }
  await page.evaluate(() => document.documentElement.setAttribute("data-theme", "light"));
  await row.click({ button: "right" });
  await page.keyboard.press("Escape");
  await expect(row).toBeFocused();
  await page.keyboard.press("Shift+F10");
  await expect(menu).toBeVisible();
  await menu.getByRole("menuitem", { name: "Настроить меню" }).click();
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
  await menu.getByRole("menuitem", { name: "Настроить меню" }).click();
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
  await menu.getByRole("menuitem", { name: "Настроить меню" }).click();
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
  await menu.getByRole("menuitem", { name: "Настроить меню" }).click();
  await expect(page.getByRole("button", { name: "Сотрудники", exact: true })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await page.goto("/users");
  await employee.click({ button: "right" });
  await menu.getByRole("menuitem", { name: "Открыть карточку" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
});
