import { expect, test } from "@playwright/test";

import { clearLoginRateLimit, DEV, loginInBrowser, OWNER } from "./helpers";

test.describe("Auth", () => {
  test.beforeEach(() => {
    clearLoginRateLimit(DEV.email);
    clearLoginRateLimit(OWNER.email);
  });

  test("dev logs in via the UI and lands on a support page", async ({ page }) => {
    await page.goto("/login", { waitUntil: "domcontentloaded" });
    await page.getByLabel("Email").fill(DEV.email);
    await page.getByRole("button", { name: /Получить код/ }).click();

    await expect(page.getByText(/Dev-режим/)).toBeVisible();
    await page.getByLabel(/Пароль/).fill(DEV.password);
    await page.getByRole("button", { name: /^Войти$/ }).click();

    await expect(page.getByRole("link", { name: "Тенанты" })).toBeVisible();
  });

  test("owner logs in via refresh cookie and sees tenant sidebar items", async ({ page }) => {
    await loginInBrowser(page, OWNER);
    await page.goto("/");

    await expect(page.getByRole("link", { name: "Точки" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Касса" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Тенанты" })).toHaveCount(0);
  });

  test("retries refresh with the same operation after a lost response", async ({ page }) => {
    await loginInBrowser(page, OWNER);
    const operationIds: string[] = [];
    let loseFirstResponse = true;

    await page.route("**/api/v1/auth/refresh", async (route) => {
      const payload = route.request().postDataJSON() as { operation_id?: string };
      if (payload.operation_id) operationIds.push(payload.operation_id);

      if (loseFirstResponse) {
        loseFirstResponse = false;
        const committedResponse = await route.fetch();
        expect(committedResponse.ok()).toBe(true);
        await route.abort("failed");
        return;
      }

      await route.continue();
    });

    await page.goto("/");

    await expect(page.getByRole("link", { name: "Касса" })).toBeVisible();
    expect(operationIds.length).toBeGreaterThanOrEqual(2);
    expect(new Set(operationIds).size).toBe(1);
    await expect(page).not.toHaveURL(/\/login/);
  });

  test("skip link moves keyboard users straight to main content", async ({ page }) => {
    await loginInBrowser(page, OWNER);
    await page.goto("/");
    await expect(page.getByRole("link", { name: "Касса" })).toBeVisible();
    await page.evaluate(() => {
      if (document.activeElement instanceof HTMLElement) {
        document.activeElement.blur();
      }
    });

    const skipLink = page.getByRole("link", { name: "Перейти к содержимому" });
    await page.keyboard.press("Tab");
    await expect(skipLink).toBeFocused();

    await page.keyboard.press("Enter");
    await expect(page.locator("main#main-content")).toBeFocused();
  });

  test("mobile drawer keeps keyboard focus inside and restores it on Escape", async ({
    page,
  }) => {
    await loginInBrowser(page, OWNER);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/");

    const openMenu = page.getByRole("button", { name: "Открыть меню" });
    await expect(openMenu).toBeVisible();
    await openMenu.focus();
    await openMenu.click();

    const dialog = page.getByRole("dialog", { name: "Меню приложения" });
    const closeMenu = dialog.getByRole("button", { name: "Закрыть меню", exact: true });
    await expect(dialog).toBeVisible();
    await expect(closeMenu).toBeFocused();

    await page.keyboard.press("Shift+Tab");
    await expect
      .poll(() => dialog.evaluate((el) => el.contains(document.activeElement)))
      .toBe(true);

    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
    await expect(openMenu).toBeFocused();
  });

  test("invalid code surfaces a friendly error", async ({ page }) => {
    await page.goto("/login", { waitUntil: "domcontentloaded" });
    await page.getByLabel("Email").fill(DEV.email);
    await page.getByRole("button", { name: /Получить код/ }).click();

    const codeField = page.getByLabel(/Код из письма/);
    await expect(codeField).toBeVisible();
    await codeField.fill("000000");
    await page.getByLabel(/Пароль/).fill(DEV.password);
    await page.getByRole("button", { name: /^Войти$/ }).click();

    await expect(page.locator(".text-danger").first()).toBeVisible();
    await expect(page).toHaveURL(/\/login/);
  });

  test("logout returns to /login", async ({ page }) => {
    await loginInBrowser(page, OWNER);
    await page.goto("/");
    await page.getByRole("button", { name: /Выйти/ }).click();
    await expect(page).toHaveURL(/\/login/);

    const access = await page.evaluate(() => window.localStorage.getItem("aurum.access_token"));
    expect(access).toBeNull();
  });
});
