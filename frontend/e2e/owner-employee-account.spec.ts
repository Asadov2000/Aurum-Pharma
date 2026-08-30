import { request, test } from "@playwright/test";

import {
  API,
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

interface Branch {
  id: string;
  name: string;
}

interface EmployeeAssignment {
  user_id: string;
}

test.describe("Owner employee accounts", () => {
  test.beforeEach(() => {
    clearLoginRateLimit(OWNER.email);
  });

  test("owner manages an employee through the complete tenant lifecycle", async ({ page }) => {
    const anonymousApi = await request.newContext();
    const ownerTokens = await apiLogin(anonymousApi, OWNER);
    const ownerApi = await apiContext(ownerTokens.access_token);
    const employeeLoginApi = await request.newContext();
    let employeeApi: Awaited<ReturnType<typeof apiContext>> | null = null;
    let resumedEmployeeApi: Awaited<ReturnType<typeof apiContext>> | null = null;

    try {
      const templatesResponse = await ownerApi.get("templates");
      expect(templatesResponse.ok()).toBe(true);
      const templates = (await templatesResponse.json()) as RoleTemplate[];
      const cashierTemplate = templates.find((template) => template.slug === "cashier");
      expect(cashierTemplate).toBeDefined();

      const branchesResponse = await ownerApi.get("branches");
      expect(branchesResponse.ok()).toBe(true);
      const branches = (await branchesResponse.json()) as Branch[];
      expect(branches.length).toBeGreaterThan(0);
      const assignedBranch = branches[0]!;
      const extraBranchResponse = await ownerApi.post("branches", {
        data: {
          name: uniqueName("Точка без доступа"),
          address: "Тестовый адрес",
          branch_type: "pharmacy",
        },
      });
      expect(extraBranchResponse.status()).toBe(201);
      const unassignedBranch = (await extraBranchResponse.json()) as Branch;

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
      const email = `${suffix}@e2e.aurum.tj`;
      await dialog.getByLabel("ФИО сотрудника").fill(fullName);
      await dialog.getByLabel("Email для входа").fill(email);
      await dialog.getByLabel("Роль").selectOption({ label: roleName });
      await dialog.getByLabel("Торговая точка").selectOption(assignedBranch.id);

      const createdResponse = page.waitForResponse(
        (response) =>
          response.url().endsWith("/api/v1/users/invite") &&
          response.request().method() === "POST" &&
          response.status() === 201,
      );
      await dialog.getByRole("button", { name: "Создать и пригласить" }).click();
      const assignment = (await (await createdResponse).json()) as EmployeeAssignment;

      await expect(
        page.getByRole("status").filter({ hasText: `Сотрудник «${fullName}» создан` }),
      ).toBeVisible();
      await expect(page.getByRole("row", { name: new RegExp(fullName) })).toContainText(
        "Ожидает активации",
      );

      const employeeTokens = await apiLogin(employeeLoginApi, { email, password: "" });
      employeeApi = await apiContext(employeeTokens.access_token);
      const employeeMeResponse = await employeeApi.get("auth/me");
      expect(employeeMeResponse.ok()).toBe(true);
      const employeeMe = (await employeeMeResponse.json()) as {
        branch_assignments: Record<string, string>;
        home_tenant_id: string;
        is_administrator: boolean;
        is_developer: boolean;
      };
      expect(employeeMe.home_tenant_id).toBeTruthy();
      expect(employeeMe.is_developer).toBe(false);
      expect(employeeMe.is_administrator).toBe(false);
      expect(Object.keys(employeeMe.branch_assignments)).toEqual([assignedBranch.id]);

      const employeeBranchesResponse = await employeeApi.get("branches");
      expect(employeeBranchesResponse.ok()).toBe(true);
      const employeeBranches = (await employeeBranchesResponse.json()) as Branch[];
      expect(employeeBranches).toEqual([expect.objectContaining(assignedBranch)]);
      expect(employeeBranches.map((branch) => branch.id)).not.toContain(unassignedBranch.id);

      await page.reload();
      let employeeRow = page.getByRole("row", { name: new RegExp(fullName) });
      await expect(employeeRow).toContainText("Активен");
      await employeeRow.getByRole("button", { name: `Действия для ${fullName}` }).click();
      await page.getByRole("menuitem", { name: "Приостановить" }).click();
      let lifecycleDialog = page.getByRole("dialog", { name: "Приостановить доступ" });
      await expect(lifecycleDialog).toContainText("Все активные сеансы завершатся");
      const suspendedResponse = page.waitForResponse(
        (response) =>
          response.url().endsWith(`/api/v1/users/${assignment.user_id}/block`) &&
          response.status() === 200,
      );
      await lifecycleDialog.getByRole("button", { name: "Приостановить" }).click();
      await suspendedResponse;
      await expect(employeeRow).toContainText("Приостановлен");
      expect((await employeeApi.get("auth/me")).status()).toBe(401);

      await employeeRow.getByRole("button", { name: `Действия для ${fullName}` }).click();
      const resumedResponse = page.waitForResponse(
        (response) =>
          response.url().endsWith(`/api/v1/users/${assignment.user_id}`) &&
          response.request().method() === "PATCH" &&
          response.status() === 200,
      );
      await page.getByRole("menuitem", { name: "Возобновить" }).click();
      await resumedResponse;
      await expect(employeeRow).toContainText("Активен");
      expect((await employeeApi.get("auth/me")).status()).toBe(401);

      const resumedTokens = await apiLogin(employeeLoginApi, { email, password: "" });
      resumedEmployeeApi = await apiContext(resumedTokens.access_token);
      expect((await resumedEmployeeApi.get("auth/me")).status()).toBe(200);

      employeeRow = page.getByRole("row", { name: new RegExp(fullName) });
      await employeeRow.getByRole("button", { name: `Действия для ${fullName}` }).click();
      await page.getByRole("menuitem", { name: "Уволить" }).click();
      lifecycleDialog = page.getByRole("dialog", { name: "Уволить сотрудника" });
      await expect(lifecycleDialog).toContainText("назначенные роли отключатся");
      const offboardedResponse = page.waitForResponse(
        (response) =>
          response.url().endsWith(`/api/v1/users/${assignment.user_id}`) &&
          response.request().method() === "DELETE" &&
          response.status() === 200,
      );
      await lifecycleDialog.getByRole("button", { name: "Уволить" }).click();
      await offboardedResponse;
      await expect(employeeRow).toContainText("Уволен");
      await expect(employeeRow).toContainText("Роли не назначены");
      expect((await resumedEmployeeApi.get("auth/me")).status()).toBe(401);

      clearLoginRateLimit(email);
      const codeResponse = await employeeLoginApi.post(`${API}/auth/login/code`, {
        data: { email },
      });
      expect(codeResponse.ok()).toBe(true);
      const { dev_code: devCode } = (await codeResponse.json()) as { dev_code: string };
      const rejectedLogin = await employeeLoginApi.post(`${API}/auth/login/verify`, {
        data: { email, code: devCode, password: "" },
      });
      expect(rejectedLogin.status()).toBe(404);
    } finally {
      await employeeApi?.dispose();
      await resumedEmployeeApi?.dispose();
      await employeeLoginApi.dispose();
      await ownerApi.dispose();
      await anonymousApi.dispose();
    }
  });
});
