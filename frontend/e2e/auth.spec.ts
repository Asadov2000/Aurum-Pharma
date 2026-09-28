import { expect, request, test } from "@playwright/test";

import {
  apiLogin,
  clearLoginRateLimit,
  currentTotp,
  DEV,
  loginInBrowser,
  logoutInBrowser,
  makeSupportSessionRequireStepUp,
  OWNER,
} from "./helpers";

// Account contacts and authentication cookies must not enter browser artifacts.
test.use({ trace: "off", screenshot: "off", video: "off" });

test.describe("Auth", () => {
  test("login remains usable on narrow screens in both themes", async ({ page }) => {
    for (const colorScheme of ["light", "dark"] as const) {
      await page.emulateMedia({ colorScheme, reducedMotion: "reduce" });
      for (const width of [320, 390, 1440]) {
        await page.setViewportSize({ width, height: 800 });
        await page.goto("/login", { waitUntil: "domcontentloaded" });
        const email = page.getByLabel("Email", { exact: true });
        const submit = page.getByRole("button", { name: "Войти", exact: true });
        await expect(email).toBeVisible();
        await expect(submit).toBeVisible();
        expect(
          await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
        ).toBe(true);
        const inputBounds = await email.boundingBox();
        const buttonBounds = await submit.boundingBox();
        expect(inputBounds?.height).toBeGreaterThanOrEqual(44);
        expect(buttonBounds?.height).toBeGreaterThanOrEqual(44);
        await email.focus();
        await page.keyboard.press("Tab");
        await expect(page.getByLabel("Пароль", { exact: true })).toBeFocused();
        await submit.focus();
        await page.keyboard.press("Enter");
        await expect(page.locator("#identity-error")).toBeVisible();
        await expect(email).toHaveAttribute("aria-invalid", "true");
      }
    }
  });

  test("opens the public website in a new tab and preserves the login form", async ({
    page,
    context,
  }) => {
    await context.route("http://127.0.0.1:4174/**", (route) =>
      route.fulfill({ contentType: "text/html", body: "<h1>Aurum Pharma public website</h1>" }),
    );
    await page.goto("/login", { waitUntil: "domcontentloaded" });
    await page.getByLabel("Email").fill("visitor@example.test");
    const originalUrl = page.url();
    const popupPromise = page.waitForEvent("popup");
    await page.getByRole("link", { name: /О проекте Aurum Pharma/ }).click();
    const popup = await popupPromise;
    await expect(popup.getByRole("heading", { name: "Aurum Pharma public website" })).toBeVisible();
    await expect(page.getByLabel("Email")).toHaveValue("visitor@example.test");
    expect(page.url()).toBe(originalUrl);
    expect(await popup.evaluate(() => window.opener === null)).toBe(true);
    await popup.close();
  });

  test("dev logs in via the UI and lands on a support page", async ({ page }) => {
    clearLoginRateLimit(DEV.email);
    await page.goto("/login", { waitUntil: "domcontentloaded" });
    await page.getByLabel("Email").fill(DEV.email);
    await page.getByLabel(/Пароль/).fill(DEV.password);
    await page.getByRole("button", { name: /^Войти$/ }).click();
    await page.getByLabel("Код подтверждения").fill(currentTotp(DEV.totpSecret!));
    await page.getByRole("button", { name: "Подтвердить" }).click();

    await expect(
      page
        .getByRole("navigation", { name: "Основная навигация" })
        .getByRole("link", { name: "Управление", exact: true }),
    ).toBeVisible();
  });

  test("owner completes MFA in the UI and keeps tenant access", async ({ page }) => {
    clearLoginRateLimit(OWNER.email);
    await page.goto("/login", { waitUntil: "domcontentloaded" });
    await page.getByLabel("Email").fill(OWNER.email);
    await page.getByLabel(/Пароль/).fill(OWNER.password);
    await page.getByRole("button", { name: /^Войти$/ }).click();
    await page.getByLabel("Код подтверждения").fill(currentTotp(OWNER.totpSecret!));
    await page.getByRole("button", { name: "Подтвердить" }).click();

    await expect(
      page
        .getByRole("navigation", { name: "Основная навигация" })
        .getByRole("link", { name: "Касса" }),
    ).toBeVisible();
    await expect(
      page
        .getByRole("navigation", { name: "Основная навигация" })
        .getByRole("link", { name: "Управление", exact: true }),
    ).toHaveCount(0);
  });

  test("retries a protected request after password confirmation", async ({ page }) => {
    await loginInBrowser(page, DEV);
    makeSupportSessionRequireStepUp(DEV.email);

    const deniedRequest = page.waitForResponse(
      (response) =>
        response.url().includes("/api/v1/admin/audit/global") && response.status() === 403,
    );
    await page.goto("/audit");
    await deniedRequest;
    await expect(
      page
        .getByRole("navigation", { name: "Основная навигация" })
        .getByRole("link", { name: "Управление", exact: true }),
    ).toBeVisible();
    await expect(page.locator("#scope")).toHaveValue("global");

    const dialog = page.getByRole("dialog", { name: "Подтверждение действия" });
    await expect(dialog).toBeVisible();

    const retriedRequest = page.waitForResponse(
      (response) =>
        response.url().includes("/api/v1/admin/audit/global") && response.status() === 200,
    );
    await dialog.getByLabel("Пароль аккаунта").fill(DEV.password);
    await dialog.getByRole("button", { name: "Подтвердить" }).click();
    await retriedRequest;
    await expect(dialog).toBeHidden();
  });

  test("owner logs in via refresh cookie and sees tenant sidebar items", async ({ page }) => {
    await loginInBrowser(page, OWNER);
    await page.goto("/");

    await expect(page.getByRole("link", { name: "Точки" })).toBeVisible();
    await expect(
      page
        .getByRole("navigation", { name: "Основная навигация" })
        .getByRole("link", { name: "Касса" }),
    ).toBeVisible();
    await expect(
      page
        .getByRole("navigation", { name: "Основная навигация" })
        .getByRole("link", { name: "Управление", exact: true }),
    ).toHaveCount(0);
  });

  test("independent desktop and mobile owner sessions keep the same branches after reload", async ({
    page,
    browser,
    baseURL,
  }) => {
    const mobileContext = await browser.newContext({
      baseURL,
      viewport: { width: 390, height: 844 },
      isMobile: true,
      hasTouch: true,
    });
    try {
      await loginInBrowser(page, OWNER);
      await page.goto("/branches");
      const desktopRows = page.getByRole("table").locator("tbody tr");
      await expect(desktopRows.first()).toBeVisible();
      const expectedRows = await desktopRows.allTextContents();
      expect(expectedRows.length).toBeGreaterThan(0);
      expect(
        (await mobileContext.cookies()).some((cookie) => cookie.name === "aurum_refresh_token"),
      ).toBe(false);

      const mobilePage = await mobileContext.newPage();
      await loginInBrowser(mobilePage, OWNER);
      await mobilePage.goto("/branches");
      const mobileRows = mobilePage.getByRole("table").locator("tbody tr");
      await expect(mobileRows).toHaveText(expectedRows);
      const desktopRefresh = (await page.context().cookies()).find(
        (cookie) => cookie.name === "aurum_refresh_token",
      );
      const mobileRefresh = (await mobileContext.cookies()).find(
        (cookie) => cookie.name === "aurum_refresh_token",
      );
      // Assert cookie isolation without printing either credential on failure.
      expect(
        Boolean(desktopRefresh && mobileRefresh && desktopRefresh.value !== mobileRefresh.value),
      ).toBe(true);

      const mobileRefreshResponse = mobilePage.waitForResponse(
        (response) =>
          new URL(response.url()).pathname === "/api/v1/auth/refresh" &&
          response.request().method() === "POST",
      );
      await mobilePage.reload();
      expect((await mobileRefreshResponse).status()).toBe(200);
      await expect(mobilePage).toHaveURL(/\/branches$/);
      await expect(mobileRows).toHaveText(expectedRows);

      const desktopRefreshResponse = page.waitForResponse(
        (response) =>
          new URL(response.url()).pathname === "/api/v1/auth/refresh" &&
          response.request().method() === "POST",
      );
      await page.reload();
      expect((await desktopRefreshResponse).status()).toBe(200);
      await expect(page).toHaveURL(/\/branches$/);
      await expect(desktopRows).toHaveText(expectedRows);
    } finally {
      await mobileContext.close();
    }
  });

  test("owner reviews and revokes other active sessions", async ({ page }) => {
    await loginInBrowser(page, OWNER);
    await apiLogin(page.request, OWNER);
    await page.goto("/security");

    await expect(
      page.getByRole("main").getByRole("heading", { name: "Безопасность", exact: true }),
    ).toBeVisible();
    const main = page.getByRole("main");
    await expect(main.getByRole("link", { name: "Мой профиль", exact: true })).toHaveAttribute(
      "href",
      "/profile",
    );
    await expect(main.getByRole("heading", { name: "Способы входа", exact: true })).toBeVisible();
    await expect(
      main.getByRole("heading", { name: "Дополнительная защита", exact: true }),
    ).toBeVisible();
    await expect(main.getByText("Текущий сеанс", { exact: true })).toBeVisible();
    for (const width of [320, 390, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      expect(
        await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
      ).toBe(true);
    }
    await page.evaluate(() => document.documentElement.setAttribute("data-theme", "dark"));
    await main.getByRole("link", { name: "Открыть настройки защиты", exact: true }).click();
    await expect(page).toHaveURL(/\/security#security-mfa$/);
    await expect(
      main.getByRole("heading", { name: "Дополнительная защита", exact: true }),
    ).toBeInViewport();
    await main.getByText("Что делать", { exact: true }).click();
    await expect(main.getByText(/На странице входа нажмите/)).toBeVisible();
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
    await expect(main.getByText("Текущий сеанс", { exact: true })).toBeVisible();
  });

  test("warns the owner about a login from a new device", async ({ page }) => {
    await loginInBrowser(page, OWNER);
    const otherDevice = await request.newContext();
    try {
      await apiLogin(otherDevice, OWNER);
    } finally {
      await otherDevice.dispose();
    }

    await page.goto("/notifications");
    const main = page.getByRole("main");
    await expect(main.getByRole("heading", { name: "Входящие", exact: true })).toBeVisible();
    await expect(
      main.getByRole("heading", { name: "Настройки уведомлений", exact: true }),
    ).toBeVisible();
    await expect(main.getByText("Обязательно", { exact: true })).toBeVisible();
    for (const theme of ["light", "dark"]) {
      await page.evaluate(
        (value) => document.documentElement.setAttribute("data-theme", value),
        theme,
      );
      for (const width of [320, 390, 1440]) {
        await page.setViewportSize({ width, height: 800 });
        expect(
          await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
        ).toBe(true);
        await expect(
          main.getByRole("button", { name: "Сохранить настройки", exact: true }),
        ).toBeVisible();
      }
    }
    const saved = page.waitForResponse(
      (response) =>
        response.url().endsWith("/notifications/subscriptions") &&
        response.request().method() === "PATCH",
    );
    await main.getByRole("button", { name: "Сохранить настройки", exact: true }).click();
    expect((await saved).status()).toBe(200);
    await expect(
      main.getByRole("status").filter({ hasText: "Настройки уведомлений сохранены." }),
    ).toBeVisible();
    const alert = page
      .getByRole("listitem")
      .filter({ hasText: "Вход с нового устройства" })
      .first();
    await expect(alert).toBeVisible();
    const read = page.waitForResponse(
      (response) =>
        /\/notifications\/[^/]+\/read$/.test(response.url()) &&
        response.request().method() === "POST",
    );
    await alert.getByRole("button", { name: "Прочитано", exact: true }).click();
    expect((await read).status()).toBe(200);
    await expect(alert.getByRole("button", { name: "Прочитано", exact: true })).toHaveCount(0);
    await alert.getByRole("link", { name: "Проверить сеансы" }).click();

    await expect(page).toHaveURL(/\/security$/);
    await expect(
      page.getByRole("main").getByRole("heading", { name: "Безопасность", exact: true }),
    ).toBeVisible();
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

    await expect(
      page
        .getByRole("navigation", { name: "Основная навигация" })
        .getByRole("link", { name: "Касса" }),
    ).toBeVisible();
    expect(operationIds.length).toBeGreaterThanOrEqual(2);
    expect(new Set(operationIds).size).toBe(1);
    await expect(page).not.toHaveURL(/\/login/);
  });

  test("skip link moves keyboard users straight to main content", async ({ page }) => {
    await loginInBrowser(page, OWNER);
    await page.goto("/");
    await expect(
      page
        .getByRole("navigation", { name: "Основная навигация" })
        .getByRole("link", { name: "Касса" }),
    ).toBeVisible();
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
    await page.getByRole("button", { name: "Первый вход без пароля" }).click();
    await page.getByLabel("Email").fill("unknown-bootstrap@e2e.aurum.tj");
    await page.getByRole("button", { name: /Получить код/ }).click();

    const codeField = page.getByLabel(/Код из письма/);
    await expect(codeField).toBeVisible();
    await codeField.fill("000000");
    await page.getByRole("button", { name: /^Войти$/ }).click();

    await expect(page.getByRole("alert").first()).toBeVisible();
    await expect(page).toHaveURL(/\/login/);
  });

  test("logout returns to /login", async ({ page }) => {
    await loginInBrowser(page, OWNER);
    await page.goto("/");
    await logoutInBrowser(page);
    await expect(page).toHaveURL(/\/login/);

    const access = await page.evaluate(() => window.localStorage.getItem("aurum.access_token"));
    expect(access).toBeNull();
  });
});
