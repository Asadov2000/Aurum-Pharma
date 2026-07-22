import { expect, test } from "@playwright/test";

import {
  apiLogin,
  clearLoginRateLimit,
  currentTotp,
  DEV,
  loginInBrowser,
  makeSupportSessionRequireStepUp,
  OWNER,
} from "./helpers";

test.describe("Auth", () => {
  test("dev logs in via the UI and lands on a support page", async ({ page }) => {
    clearLoginRateLimit(DEV.email);
    await page.goto("/login", { waitUntil: "domcontentloaded" });
    await page.getByLabel("Email").fill(DEV.email);
    await page.getByRole("button", { name: /Получить код/ }).click();

    await expect(page.getByText(/Dev-режим/)).toBeVisible();
    await page.getByLabel(/Пароль/).fill(DEV.password);
    await page.getByRole("button", { name: /^Войти$/ }).click();
    await page.getByLabel("Код подтверждения").fill(currentTotp(DEV.totpSecret!));
    await page.getByRole("button", { name: "Подтвердить" }).click();

    await expect(page.getByRole("link", { name: "Тенанты" })).toBeVisible();
  });

  test("retries a protected request after global MFA step-up", async ({ page }) => {
    await loginInBrowser(page, DEV);
    makeSupportSessionRequireStepUp(DEV.email);

    const deniedRequest = page.waitForResponse(
      (response) =>
        response.url().includes("/api/v1/admin/audit/global") && response.status() === 403,
    );
    await page.goto("/audit");
    await deniedRequest;
    await expect(page.getByRole("link", { name: "Тенанты" })).toBeVisible();
    await expect(page.locator("#scope")).toHaveValue("global");

    const dialog = page.getByRole("dialog", { name: "Подтверждение действия" });
    await expect(dialog).toBeVisible();

    const retriedRequest = page.waitForResponse(
      (response) =>
        response.url().includes("/api/v1/admin/audit/global") && response.status() === 200,
    );
    await dialog.getByLabel("Код подтверждения").fill(currentTotp(DEV.totpSecret!));
    await dialog.getByRole("button", { name: "Подтвердить" }).click();
    await retriedRequest;
    await expect(dialog).toBeHidden();
  });

  test("owner logs in via refresh cookie and sees tenant sidebar items", async ({ page }) => {
    await loginInBrowser(page, OWNER);
    await page.goto("/");

    await expect(page.getByRole("link", { name: "Точки" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Касса" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Тенанты" })).toHaveCount(0);
  });

  test("owner reviews and revokes other active sessions", async ({ page }) => {
    await loginInBrowser(page, OWNER);
    await apiLogin(page.request, OWNER);
    await page.goto("/security");

    await expect(page.getByRole("heading", { name: "Безопасность" })).toBeVisible();
    const revokeOthers = page.getByRole("button", { name: "Завершить остальные" });
    await expect(revokeOthers).toBeEnabled();
    await revokeOthers.click();

    const dialog = page.getByRole("dialog", { name: "Завершить остальные сеансы?" });
    await expect(dialog).toBeVisible();
    const revokedResponse = page.waitForResponse(
      (response) =>
        response.url().endsWith("/api/v1/auth/sessions/revoke-others") && response.status() === 200,
    );
    await dialog.getByRole("button", { name: "Завершить остальные" }).click();
    const revoked = (await (await revokedResponse).json()) as { revoked_count: number };

    expect(revoked.revoked_count).toBeGreaterThan(0);
    await expect(page.getByText(/Завершено сеансов:/)).toBeVisible();
    await expect(page.getByText("Сеансы (1)")).toBeVisible();
    await expect(page.getByText("Текущий сеанс")).toBeVisible();
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

  test("mobile drawer keeps keyboard focus inside and restores it on Escape", async ({ page }) => {
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
    clearLoginRateLimit(DEV.email);
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
