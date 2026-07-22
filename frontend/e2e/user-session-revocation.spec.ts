import { request, test } from "@playwright/test";

import { apiContext, apiLogin, DEV, expect, loginInBrowser, OWNER, uniqueName } from "./helpers";

test.describe("Employee session security", () => {
  test("owner ends an employee's sessions but cannot target the owner account", async ({
    page,
  }) => {
    const ownerAnon = await request.newContext();
    const developerAnon = await request.newContext();
    const ownerTokens = await apiLogin(ownerAnon, OWNER);
    const developerTokens = await apiLogin(developerAnon, DEV);
    const ownerApi = await apiContext(ownerTokens.access_token);
    const developerApi = await apiContext(developerTokens.access_token);

    try {
      const meResponse = await ownerApi.get("auth/me");
      expect(meResponse.ok()).toBe(true);
      const me = (await meResponse.json()) as { home_tenant_id: string };
      const fullName = uniqueName("Session Employee");
      const email = `${fullName.toLowerCase().replaceAll(" ", "-")}@e2e.aurum.tj`;
      const createResponse = await developerApi.post(`admin/tenants/${me.home_tenant_id}/members`, {
        data: { email, full_name: fullName },
      });
      expect(createResponse.status()).toBe(201);
      const employee = (await createResponse.json()) as { user_id: string };

      const activateResponse = await ownerApi.patch(`users/${employee.user_id}`, {
        data: { status: "active" },
      });
      expect(activateResponse.ok()).toBe(true);

      await loginInBrowser(page, OWNER);
      await page.goto("/users");
      await expect(page.getByRole("heading", { name: "Сотрудники" })).toBeVisible();

      const ownerRow = page.getByRole("row", { name: /Demo Owner/ });
      await ownerRow.getByRole("button", { name: "Действия для Demo Owner" }).click();
      const ownerMenu = page.getByRole("menu", { name: "Действия для Demo Owner" });
      await expect(ownerMenu.getByRole("menuitem", { name: "Завершить сеансы" })).toHaveCount(0);
      await page.keyboard.press("Escape");

      const employeeRow = page.getByRole("row", { name: new RegExp(fullName) });
      const employeeActions = employeeRow.getByRole("button", {
        name: `Действия для ${fullName}`,
      });
      await employeeActions.click();
      await expect(page.getByRole("menuitem", { name: "Завершить сеансы" })).toBeVisible();
      await page.getByRole("menuitem", { name: "Завершить сеансы" }).click();

      const dialog = page.getByRole("dialog", { name: "Завершить активные сеансы" });
      await expect(dialog.getByText(/будет немедленно выведен из системы/i)).toBeVisible();
      const revokedResponse = page.waitForResponse(
        (response) =>
          response.url().endsWith(`/api/v1/users/${employee.user_id}/sessions/revoke`) &&
          response.status() === 200,
      );
      await dialog.getByRole("button", { name: "Завершить сеансы" }).click();
      const body = (await (await revokedResponse).json()) as { revoked_count: number };

      expect(body.revoked_count).toBe(0);
      await expect(page.getByRole("status")).toHaveText("У сотрудника нет активных сеансов.");
    } finally {
      await ownerApi.dispose();
      await developerApi.dispose();
      await ownerAnon.dispose();
      await developerAnon.dispose();
    }
  });
});
