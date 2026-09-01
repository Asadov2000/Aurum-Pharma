import { request, test, type Locator } from "@playwright/test";

import {
  apiContext,
  apiLogin,
  CASHIER,
  clearLoginRateLimit,
  expect,
  loginInBrowser,
  OWNER,
  uniqueName,
} from "./helpers";

interface Branch {
  id: string;
  name: string;
}

interface TenantRole {
  id: string;
  name: string;
}

interface UserAssignment {
  branch_id: string | null;
  id: string;
  is_active: boolean;
  role_id: string;
}

interface TenantUser {
  assignments: UserAssignment[];
  email: string;
  full_name: string;
  id: string;
}

interface UserListResponse {
  items: TenantUser[];
}

interface CurrentUser {
  branch_assignments: Record<string, string>;
  permissions: string[];
}

test.describe("Owner employee access assignment", () => {
  test.beforeEach(() => {
    clearLoginRateLimit(OWNER.email);
    clearLoginRateLimit(CASHIER.email);
  });

  test("assigns, blocks a conflicting scope, revokes, and applies the new access immediately", async ({
    page,
  }) => {
    const anonymousApi = await request.newContext();
    const ownerTokens = await apiLogin(anonymousApi, OWNER);
    const ownerApi = await apiContext(ownerTokens.access_token);
    let cashierApi: Awaited<ReturnType<typeof apiContext>> | null = null;

    try {
      const branchesResponse = await ownerApi.get("branches");
      expect(branchesResponse.ok()).toBe(true);
      const branches = (await branchesResponse.json()) as Branch[];
      expect(branches.length).toBeGreaterThan(0);
      const branch = branches[0]!;

      const usersResponse = await ownerApi.get("users");
      expect(usersResponse.ok()).toBe(true);
      const users = (await usersResponse.json()) as UserListResponse;
      const cashier = users.items.find((user) => user.email === CASHIER.email);
      expect(cashier).toBeDefined();

      for (const assignment of cashier!.assignments.filter((item) => item.is_active)) {
        const revokeResponse = await ownerApi.delete(
          `users/${cashier!.id}/assignments/${assignment.id}`,
        );
        expect(revokeResponse.ok()).toBe(true);
      }

      const reportsRole = await createRole(
        ownerApi,
        uniqueName("Просмотр отчётов"),
        "reports.view",
      );
      const salesRole = await createRole(ownerApi, uniqueName("Продажи"), "pos.sell");

      await loginInBrowser(page, OWNER);
      await page.goto("/users");
      const employeeRow = page.getByRole("row", { name: new RegExp(cashier!.full_name) });
      await expect(employeeRow).toBeVisible();
      await employeeRow.getByRole("button", { name: `Действия для ${cashier!.full_name}` }).click();
      await page.getByRole("menuitem", { name: "Настроить доступ" }).click();

      const accessDialog = page.getByRole("dialog", {
        name: `Доступ сотрудника: ${cashier!.full_name}`,
      });
      await expect(accessDialog).toBeVisible();
      let assignmentPosts = 0;
      page.on("request", (requestEvent) => {
        if (
          requestEvent.method() === "POST" &&
          requestEvent.url().endsWith(`/api/v1/users/${cashier!.id}/assignments`)
        ) {
          assignmentPosts += 1;
        }
      });

      await assignRole(accessDialog, reportsRole, branch);
      await expect(accessDialog.getByRole("status")).toContainText(
        `Роль «${reportsRole.name}» назначена`,
      );
      expect(assignmentPosts).toBe(1);

      const cashierTokens = await apiLogin(anonymousApi, CASHIER);
      cashierApi = await apiContext(cashierTokens.access_token);
      await expectCurrentAccess(cashierApi, {
        branchId: branch.id,
        grantedPermission: "reports.view",
        rejectedPermission: "pos.sell",
        roleId: reportsRole.id,
      });

      await accessDialog.getByRole("button", { name: "Назначить роль" }).click();
      await accessDialog.getByLabel("Роль", { exact: true }).selectOption(salesRole.id);
      await accessDialog.getByLabel("Где действует роль").selectOption(branch.id);
      await accessDialog.getByRole("button", { name: "Проверить доступ" }).click();
      await expect(accessDialog.getByRole("alert")).toContainText(
        `Для выбранной области уже действует роль «${reportsRole.name}»`,
      );
      expect(assignmentPosts).toBe(1);
      await accessDialog.getByRole("button", { name: "Отмена" }).click();

      const activeAssignment = accessDialog
        .getByRole("listitem")
        .filter({ hasText: reportsRole.name });
      await activeAssignment.getByRole("button", { name: "Отозвать" }).click();
      const revokeDialog = page.getByRole("dialog", { name: "Отозвать роль" });
      const revokeResponse = page.waitForResponse(
        (response) =>
          response.request().method() === "DELETE" &&
          response.url().includes(`/api/v1/users/${cashier!.id}/assignments/`) &&
          response.ok(),
      );
      await revokeDialog.getByRole("button", { name: "Отозвать" }).click();
      await revokeResponse;
      await expect(accessDialog.getByRole("status")).toContainText(
        `Роль «${reportsRole.name}» отозвана`,
      );
      await expectCurrentAccess(cashierApi, {
        rejectedPermission: "reports.view",
      });

      await assignRole(accessDialog, salesRole, branch);
      expect(assignmentPosts).toBe(2);
      await expectCurrentAccess(cashierApi, {
        branchId: branch.id,
        grantedPermission: "pos.sell",
        rejectedPermission: "reports.view",
        roleId: salesRole.id,
      });
    } finally {
      await cashierApi?.dispose();
      await ownerApi.dispose();
      await anonymousApi.dispose();
    }
  });
});

async function createRole(
  ownerApi: Awaited<ReturnType<typeof apiContext>>,
  name: string,
  permission: string,
): Promise<TenantRole> {
  const response = await ownerApi.post("roles", {
    data: { description: null, name, permissions: [permission] },
  });
  expect(response.status()).toBe(201);
  return (await response.json()) as TenantRole;
}

async function assignRole(dialog: Locator, role: TenantRole, branch: Branch): Promise<void> {
  await dialog.getByRole("button", { name: "Назначить роль" }).click();
  await dialog.getByLabel("Роль", { exact: true }).selectOption(role.id);
  await dialog.getByLabel("Где действует роль").selectOption(branch.id);
  await dialog.getByRole("button", { name: "Проверить доступ" }).click();
  const confirmation = dialog.page().getByRole("dialog", {
    name: "Проверьте доступ сотрудника",
  });
  await expect(confirmation).toContainText(role.name);
  const response = dialog
    .page()
    .waitForResponse(
      (apiResponse) =>
        apiResponse.request().method() === "POST" &&
        apiResponse.url().includes("/api/v1/users/") &&
        apiResponse.url().endsWith("/assignments") &&
        apiResponse.status() === 201,
    );
  await confirmation.getByRole("button", { name: "Назначить роль" }).click();
  await response;
}

async function expectCurrentAccess(
  api: Awaited<ReturnType<typeof apiContext>>,
  expected: {
    branchId?: string;
    grantedPermission?: string;
    rejectedPermission: string;
    roleId?: string;
  },
): Promise<void> {
  const response = await api.get("auth/me");
  expect(response.ok()).toBe(true);
  const currentUser = (await response.json()) as CurrentUser;
  if (expected.grantedPermission) {
    expect(currentUser.permissions).toContain(expected.grantedPermission);
  }
  expect(currentUser.permissions).not.toContain(expected.rejectedPermission);
  if (expected.branchId && expected.roleId) {
    expect(currentUser.branch_assignments).toEqual({ [expected.branchId]: expected.roleId });
  } else {
    expect(currentUser.branch_assignments).toEqual({});
  }
}
