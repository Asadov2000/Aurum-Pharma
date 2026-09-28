import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import {
  clearLoginRateLimit,
  currentTotp,
  loginInBrowser,
  logoutInBrowser,
  OWNER,
} from "./helpers";

// Authentication credentials and cookies must not enter captured artifacts.
test.use({ trace: "off", screenshot: "off", video: "off" });

const logoutRoute = "**/api/v1/auth/logout";
const refreshPath = "/api/v1/auth/refresh";

async function hasRefreshCookie(context: BrowserContext): Promise<boolean> {
  return (await context.cookies()).some((cookie) => cookie.name === "aurum_refresh_token");
}

async function expectSignedIn(page: Page): Promise<void> {
  await expect(
    page
      .getByRole("navigation", { name: "Основная навигация" })
      .getByRole("link", { name: "Касса", exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "Меню профиля", exact: true })).toBeVisible();
}

async function expectSignedOut(page: Page): Promise<void> {
  await expect(page).toHaveURL(/\/login(?:\?|$)/);
  await expect(page.getByLabel("Email", { exact: true })).toBeVisible();
  await expect(
    page
      .getByRole("navigation", { name: "Основная навигация" })
      .getByRole("link", { name: "Касса", exact: true }),
  ).toHaveCount(0);
}

async function loginExplicitly(page: Page): Promise<void> {
  clearLoginRateLimit(OWNER.email);
  const email = page.getByLabel("Email", { exact: true });
  const password = page.getByLabel("Пароль", { exact: true });
  await expect(email).toBeVisible();
  await expect(password).toBeVisible();
  // DOM input events keep credential values out of Playwright action call logs.
  for (const [input, value] of [
    [email, OWNER.email],
    [password, OWNER.password],
  ] as const) {
    await input.evaluate((element, nextValue) => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
      if (!setter) throw new Error("Input value setter is unavailable");
      setter.call(element, nextValue);
      element.dispatchEvent(new Event("input", { bubbles: true }));
      element.dispatchEvent(new Event("change", { bubbles: true }));
    }, value);
  }
  await page.getByRole("button", { name: "Войти", exact: true }).click();
  const code = page.getByLabel("Код подтверждения", { exact: true });
  await expect(code).toBeVisible();
  if (!OWNER.totpSecret) throw new Error("Owner MFA fixture is unavailable");
  await code.evaluate((element, nextValue) => {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
    if (!setter) throw new Error("Input value setter is unavailable");
    setter.call(element, nextValue);
    element.dispatchEvent(new Event("input", { bubbles: true }));
    element.dispatchEvent(new Event("change", { bubbles: true }));
  }, currentTotp(OWNER.totpSecret));
  await page.getByRole("button", { name: "Подтвердить", exact: true }).click();
  await expectSignedIn(page);
}

test.describe("Durable browser logout", () => {
  test("failed server logout survives reload and permits a later explicit login", async ({
    page,
  }) => {
    await loginInBrowser(page, OWNER);
    await page.goto("/");
    await expectSignedIn(page);
    expect(await hasRefreshCookie(page.context())).toBe(true);

    let blockLogout = true;
    let logoutAttempts = 0;
    let refreshRequests = 0;
    page.context().on("request", (request) => {
      if (new URL(request.url()).pathname === refreshPath) refreshRequests += 1;
    });
    await page.context().route(logoutRoute, async (route) => {
      logoutAttempts += 1;
      if (blockLogout) await route.abort("failed");
      else await route.continue();
    });

    await logoutInBrowser(page);
    await expectSignedOut(page);
    await expect.poll(() => logoutAttempts).toBeGreaterThanOrEqual(1);
    // A failed request leaves the real httpOnly cookie in the browser.
    expect(await hasRefreshCookie(page.context())).toBe(true);

    await page.reload({ waitUntil: "domcontentloaded" });
    await expectSignedOut(page);
    await expect.poll(() => logoutAttempts).toBeGreaterThanOrEqual(2);
    expect(refreshRequests).toBe(0);
    expect(await hasRefreshCookie(page.context())).toBe(true);

    blockLogout = false;
    const serverLogout = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === "/api/v1/auth/logout" &&
        response.request().method() === "POST",
    );
    await loginExplicitly(page);
    expect((await serverLogout).status()).toBe(200);
    expect(await hasRefreshCookie(page.context())).toBe(true);

    const restoredRefresh = page.waitForResponse(
      (response) => new URL(response.url()).pathname === refreshPath,
    );
    await page.reload({ waitUntil: "domcontentloaded" });
    expect((await restoredRefresh).status()).toBe(200);
    await expectSignedIn(page);
  });

  test("logout immediately closes both tabs sharing the browser session", async ({ page }) => {
    await loginInBrowser(page, OWNER);
    await page.goto("/");
    await expectSignedIn(page);
    const otherPage = await page.context().newPage();
    await otherPage.goto("/");
    await expectSignedIn(otherPage);

    let refreshRequests = 0;
    page.context().on("request", (request) => {
      if (new URL(request.url()).pathname === refreshPath) refreshRequests += 1;
    });
    let releaseLogout!: () => void;
    const logoutGate = new Promise<void>((resolve) => {
      releaseLogout = resolve;
    });
    await page.context().route(logoutRoute, async (route) => {
      await logoutGate;
      await route.continue();
    });
    try {
      await logoutInBrowser(page);
      await expectSignedOut(page);
      await expectSignedOut(otherPage);
      expect(await hasRefreshCookie(page.context())).toBe(true);
    } finally {
      releaseLogout();
    }
    await expect.poll(() => hasRefreshCookie(page.context())).toBe(false);

    await otherPage.reload({ waitUntil: "domcontentloaded" });
    await expectSignedOut(otherPage);
    expect(refreshRequests).toBe(0);
    await otherPage.close();
  });

  test("offline logout remains signed out after connection recovery and reload", async ({
    page,
  }) => {
    // Complete a real UI login so the lazy login route is already loaded before
    // disconnecting. Loading a new JS chunk is not part of offline logout.
    await page.goto("/login", { waitUntil: "domcontentloaded" });
    await loginExplicitly(page);
    let refreshRequests = 0;
    page.context().on("request", (request) => {
      if (new URL(request.url()).pathname === refreshPath) refreshRequests += 1;
    });

    try {
      await page.context().setOffline(true);
      await logoutInBrowser(page);
      await expectSignedOut(page);
      expect(await hasRefreshCookie(page.context())).toBe(true);
    } finally {
      await page.context().setOffline(false);
    }

    // HTML is not cached by the current SW; reload only after restoring transport.
    // The API-only outage test above exercises bootstrap with logout still pending.
    await page.reload({ waitUntil: "domcontentloaded" });
    await expectSignedOut(page);
    await expect.poll(() => hasRefreshCookie(page.context())).toBe(false);
    expect(refreshRequests).toBe(0);
  });
});
