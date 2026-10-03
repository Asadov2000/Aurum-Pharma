import { request, test } from "@playwright/test";

import {
  apiContext,
  apiLogin,
  catalogSearchKey,
  clearLoginRateLimit,
  expect,
  loginInBrowser,
  OWNER,
  uniqueName,
} from "./helpers";

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
  await page.getByLabel(/Поиск/).fill(catalogSearchKey(name));
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
  await page.getByLabel(/Поиск/).fill(catalogSearchKey(name));
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
