import { request, test } from "@playwright/test";

import {
  apiContext,
  apiLogin,
  clearLoginRateLimit,
  expect,
  loginInBrowser,
  OWNER,
  uniqueName,
} from "./helpers";

interface RoleTemplate {
  description: string | null;
  permissions: string[];
  slug: string;
}

test.describe("Owner employee accounts", () => {
  test.beforeEach(() => {
    clearLoginRateLimit(OWNER.email);
  });

  test("owner creates an employee only inside the current pharmacy", async ({ page }) => {
    const anonymousApi = await request.newContext();
    const ownerTokens = await apiLogin(anonymousApi, OWNER);
    const ownerApi = await apiContext(ownerTokens.access_token);
    const templatesResponse = await ownerApi.get("templates");
    expect(templatesResponse.ok()).toBe(true);
    const templates = (await templatesResponse.json()) as RoleTemplate[];
    const cashierTemplate = templates.find((template) => template.slug === "cashier");
    expect(cashierTemplate).toBeDefined();
    const roleName = uniqueName("Сотрудник аптеки");
    const roleResponse = await ownerApi.post("roles", {
      data: {
        name: roleName,
        description: cashierTemplate?.description ?? null,
        permissions: cashierTemplate?.permissions ?? [],
      },
    });
    expect(roleResponse.ok()).toBe(true);

    await loginInBrowser(page, OWNER);
    await page.goto("/users");

    await page.getByRole("button", { name: /Добавить сотрудника/ }).click();
    const dialog = page.getByRole("dialog", { name: "Новый сотрудник" });
    await expect(dialog).toContainText("Аккаунт будет привязан только к этой аптеке");

    const suffix = uniqueName("employee").toLowerCase().replaceAll(" ", "-");
    const fullName = `Сотрудник ${suffix}`;
    await dialog.getByLabel("ФИО сотрудника").fill(fullName);
    await dialog.getByLabel("Email для входа").fill(`${suffix}@e2e.aurum.tj`);

    const role = dialog.getByLabel("Роль");
    await role.selectOption({ label: roleName });

    const created = page.waitForResponse(
      (response) =>
        response.url().endsWith("/api/v1/users/invite") &&
        response.request().method() === "POST" &&
        response.status() === 201,
    );
    await dialog.getByRole("button", { name: "Создать и пригласить" }).click();
    await created;

    await expect(
      page.getByRole("status").filter({ hasText: `Сотрудник «${fullName}» создан` }),
    ).toBeVisible();
    await expect(page.getByRole("row", { name: new RegExp(fullName) })).toBeVisible();

    await ownerApi.dispose();
    await anonymousApi.dispose();
  });
});
