import { test, expect, request } from "@playwright/test";

import { apiLogin, clearLoginRateLimit, DEV, OWNER } from "./helpers";

test.describe("Auth", () => {
  test.beforeEach(() => {
    // Each test that hits /auth/login/code needs the minute bucket fresh.
    clearLoginRateLimit(DEV.email);
    clearLoginRateLimit(OWNER.email);
  });

  test("dev logs in via the UI and lands on a support page", async ({ page }) => {
    // We use the UI form because that path itself must work — sidebar items
    // and route guard wiring all key off this flow.
    await page.goto("/login");
    await page.getByLabel("Email").fill(DEV.email);
    await page.getByRole("button", { name: /Получить код/ }).click();

    // Dev-mode banner pre-fills the code; just type the password and submit.
    await expect(page.getByText(/Dev-режим/)).toBeVisible();
    await page.getByLabel(/Пароль/).fill(DEV.password);
    await page.getByRole("button", { name: /^Войти$/ }).click();

    // Sidebar only shows «Тенанты» for support users — its presence is a
    // strong assertion that the user is logged in AND classified correctly.
    await expect(page.getByRole("link", { name: "Тенанты" })).toBeVisible();
  });

  test("owner logs in via API token injection and sees tenant sidebar items", async ({ page }) => {
    const api = await request.newContext();
    try {
      const tokens = await apiLogin(api, OWNER);
      await page.goto("/login");
      await page.evaluate((t) => {
        window.localStorage.setItem("aurum.access_token", t.access_token);
        window.localStorage.setItem("aurum.refresh_token", t.refresh_token);
      }, tokens);
      await page.goto("/");
      // Owner has a home_tenant_id → these tenant items must appear.
      await expect(page.getByRole("link", { name: "Точки" })).toBeVisible();
      await expect(page.getByRole("link", { name: "Касса" })).toBeVisible();
      // Owner is NOT support — admin shortcut must be absent.
      await expect(page.getByRole("link", { name: "Тенанты" })).toHaveCount(0);
    } finally {
      await api.dispose();
    }
  });

  test("mobile drawer keeps keyboard focus inside and restores it on Escape", async ({
    page,
  }) => {
    const api = await request.newContext();
    try {
      const tokens = await apiLogin(api, OWNER);
      await page.setViewportSize({ width: 390, height: 844 });
      await page.goto("/login");
      await page.evaluate((t) => {
        window.localStorage.setItem("aurum.access_token", t.access_token);
        window.localStorage.setItem("aurum.refresh_token", t.refresh_token);
      }, tokens);
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
    } finally {
      await api.dispose();
    }
  });

  test("invalid code surfaces a friendly error", async ({ page }) => {
    await page.goto("/login");
    await page.getByLabel("Email").fill(DEV.email);
    await page.getByRole("button", { name: /Получить код/ }).click();

    // Wait for the dev-code field to mount, then overwrite with garbage.
    const codeField = page.getByLabel(/Код из письма/);
    await expect(codeField).toBeVisible();
    await codeField.fill("000000");
    await page.getByLabel(/Пароль/).fill(DEV.password);
    await page.getByRole("button", { name: /^Войти$/ }).click();

    // Error text now uses the semantic danger token class.
    await expect(page.locator(".text-danger").first()).toBeVisible();
    // Still on /login.
    await expect(page).toHaveURL(/\/login/);
  });

  test("logout returns to /login", async ({ page }) => {
    // Inject tokens to skip the form.
    const api = await request.newContext();
    try {
      const tokens = await apiLogin(api, OWNER);
      await page.goto("/login");
      await page.evaluate((t) => {
        window.localStorage.setItem("aurum.access_token", t.access_token);
        window.localStorage.setItem("aurum.refresh_token", t.refresh_token);
      }, tokens);
      await page.goto("/");
      await page.getByRole("button", { name: /Выйти/ }).click();
      await expect(page).toHaveURL(/\/login/);
      // Tokens must be wiped — otherwise the route guard would bounce back to /.
      const access = await page.evaluate(() => window.localStorage.getItem("aurum.access_token"));
      expect(access).toBeNull();
    } finally {
      await api.dispose();
    }
  });
});
