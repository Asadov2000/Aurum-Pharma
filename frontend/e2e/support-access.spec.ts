import { request, test } from "@playwright/test";

import { apiContext, apiLogin, clearLoginRateLimit, DEV, expect, loginInBrowser } from "./helpers";

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
    await row.getByRole("button", { name: "Открыть доступ" }).click();

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
});
