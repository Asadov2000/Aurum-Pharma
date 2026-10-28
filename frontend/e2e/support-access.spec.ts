import { request, test } from "@playwright/test";

import {
  apiContext,
  apiLogin,
  clearLoginRateLimit,
  DEV,
  expect,
  loginInBrowser,
  logoutInBrowser,
  selectActionMenuItem,
} from "./helpers";

test.describe("Scoped tenant support access", () => {
  test.beforeEach(() => {
    clearLoginRateLimit(DEV.email);
  });

  test("opens only the role constructor and revokes the context on exit", async ({ page }) => {
    const tokens = await loginInBrowser(page, DEV);
    const bootstrapApi = await apiContext(tokens.access_token);
    const tenantResponse = await bootstrapApi.get("admin/tenants?limit=500");
    expect(tenantResponse.ok()).toBe(true);
    const tenants = (await tenantResponse.json()) as Array<{
      id: string;
      name: string;
      status: string;
    }>;
    const tenant = tenants.find(({ status }) => status !== "archived");
    await bootstrapApi.dispose();
    if (!tenant) throw new Error("E2E tenant is unavailable");

    await page.goto("/admin/tenants");
    await page.getByLabel("Поиск (название или email)").fill(tenant.name);
    const row = page.getByRole("row").filter({ hasText: tenant.name });
    await expect(row).toBeVisible();
    await selectActionMenuItem(page, `Действия для ${tenant.name}`, "Открыть защищённый доступ");

    const dialog = page.getByRole("dialog", { name: "Защищённый доступ" });
    await dialog.getByLabel("Роли и назначения").check();
    await dialog
      .getByLabel("Причина доступа")
      .fill("Настройка конструктора ролей перед пилотным запуском");
    await dialog.getByLabel("Срок").selectOption("5");

    const startResponsePromise = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        response.url().includes("/admin/support-access/sessions"),
    );
    await dialog.getByRole("button", { name: "Открыть доступ" }).click();
    const startResponse = await startResponsePromise;
    expect(startResponse.status()).toBe(201);
    const supportSession = (await startResponse.json()) as { id: string };

    await expect(page).toHaveURL(/\/roles$/);
    await expect(page.getByRole("heading", { name: "Роли", exact: true })).toBeVisible();
    const banner = page.getByRole("status").filter({ hasText: tenant.name });
    await expect(banner).toContainText("защищённый доступ");
    await expect(page.getByRole("link", { name: "Касса" })).toHaveCount(0);

    await banner.getByRole("button", { name: "Завершить" }).click();
    await expect(page).toHaveURL(/\/admin\/tenants$/);
    await expect(page.getByRole("status").filter({ hasText: tenant.name })).toHaveCount(0);

    const reloginContext = await request.newContext();
    const probeTokens = await apiLogin(reloginContext, DEV);
    const probeApi = await apiContext(probeTokens.access_token);
    try {
      const revokedProbe = await probeApi.get("roles", {
        headers: { "X-Aurum-Support-Session": supportSession.id },
      });
      expect(revokedProbe.status()).toBe(403);
    } finally {
      await probeApi.dispose();
      await reloginContext.dispose();
    }
  });

  test("a late support activation failure cannot undo logout from another tab", async ({
    page,
    context,
  }) => {
    const tokens = await loginInBrowser(page, DEV);
    const bootstrapApi = await apiContext(tokens.access_token);
    let tenant: { id: string; name: string } | undefined;
    try {
      const response = await bootstrapApi.get("admin/tenants?limit=500");
      expect(response.ok()).toBe(true);
      const tenants = (await response.json()) as Array<{
        id: string;
        name: string;
        status: string;
      }>;
      tenant = tenants.find(({ status }) => status !== "archived");
    } finally {
      await bootstrapApi.dispose();
    }
    if (!tenant) throw new Error("E2E tenant is unavailable");

    // Bootstrap both sessions before testing late activation versus logout,
    // matching the two-tab logout setup without overlapping initial refreshes.
    await page.goto("/admin/tenants");
    await expect(page.getByLabel("Поиск (название или email)")).toBeVisible();
    await expect(page.getByLabel("Поиск (название или email)")).toBeEnabled();
    const otherTab = await context.newPage();
    await otherTab.goto("/admin/tenants");
    await expect(otherTab.getByRole("button", { name: "Меню профиля", exact: true })).toBeVisible();
    await expect(otherTab.getByLabel("Поиск (название или email)")).toBeVisible();
    await expect(otherTab.getByLabel("Поиск (название или email)")).toBeEnabled();

    let releaseIdentity!: () => void;
    const pendingIdentity = new Promise<void>((resolve) => {
      releaseIdentity = resolve;
    });
    let markIdentityStarted!: () => void;
    const identityStarted = new Promise<void>((resolve) => {
      markIdentityStarted = resolve;
    });
    await page.route("**/api/v1/auth/me", async (route) => {
      if (!route.request().headers()["x-aurum-support-session"]) {
        await route.continue();
        return;
      }
      markIdentityStarted();
      await pendingIdentity;
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Service temporarily unavailable" }),
      });
    });

    try {
      await page.getByLabel("Поиск (название или email)").fill(tenant.name);
      await selectActionMenuItem(page, `Действия для ${tenant.name}`, "Открыть защищённый доступ");
      const dialog = page.getByRole("dialog", { name: "Защищённый доступ" });
      await dialog.getByLabel("Роли и назначения").check();
      await dialog.getByLabel("Причина доступа").fill("Проверка выхода из защищённого доступа");
      await dialog.getByLabel("Срок").selectOption("5");
      await dialog.getByRole("button", { name: "Открыть доступ" }).click();
      await identityStarted;

      await logoutInBrowser(otherTab);
      await expect(otherTab).toHaveURL(/\/login/);
      await expect(page).toHaveURL(/\/login/);

      const lateResponse = page.waitForResponse(
        (response) => response.url().includes("/auth/me") && response.status() === 503,
      );
      releaseIdentity();
      await lateResponse;
      // Let the failed request and any rollback request finish before navigating;
      // otherwise navigation could destroy the stale callback under test.
      await page.waitForLoadState("networkidle");
      await expect(page.getByRole("button", { name: "Меню профиля", exact: true })).toHaveCount(0);
      await expect(page.getByRole("status").filter({ hasText: "защищённый доступ" })).toHaveCount(
        0,
      );

      await page.goto("/admin/tenants");
      await expect(page).toHaveURL(/\/login/);
      await page.reload();
      await expect(page).toHaveURL(/\/login/);
      await expect(page.getByRole("button", { name: "Меню профиля", exact: true })).toHaveCount(0);
    } finally {
      releaseIdentity();
      await otherTab.close();
    }
  });
});
