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

  test("assigns one role to multiple branches and replaces it atomically", async ({ page }) => {
    const anonymousApi = await request.newContext();
    const ownerTokens = await apiLogin(anonymousApi, OWNER);
    const ownerApi = await apiContext(ownerTokens.access_token);
    let cashierApi: Awaited<ReturnType<typeof apiContext>> | null = null;

    try {
      const branchesResponse = await ownerApi.get("branches");
      expect(branchesResponse.ok()).toBe(true);
      const branches = (await branchesResponse.json()) as Branch[];
      if (branches.length < 2) {
        const createBranchResponse = await ownerApi.post("branches", {
          data: {
            name: uniqueName("Точка для проверки доступа"),
            address: "Душанбе, тестовый адрес",
            branch_type: "pharmacy",
          },
        });
        expect(createBranchResponse.ok()).toBe(true);
        branches.push((await createBranchResponse.json()) as Branch);
      }
      const selectedBranches = branches.slice(0, 2);

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
      let assignmentReplacements = 0;
      page.on("request", (requestEvent) => {
        if (
          requestEvent.method() === "PUT" &&
          requestEvent.url().endsWith(`/api/v1/users/${cashier!.id}/assignments`)
        ) {
          assignmentReplacements += 1;
        }
      });

      await assignRole(accessDialog, reportsRole, selectedBranches, false);
      await expect(accessDialog.getByRole("status")).toContainText(
        `Роль «${reportsRole.name}» применена`,
      );
      expect(assignmentReplacements).toBe(1);

      const cashierTokens = await apiLogin(anonymousApi, CASHIER);
      cashierApi = await apiContext(cashierTokens.access_token);
      await expectCurrentAccess(cashierApi, {
        branchAssignments: Object.fromEntries(
          selectedBranches.map((branch) => [branch.id, reportsRole.id]),
        ),
        grantedPermission: "reports.view",
        rejectedPermission: "pos.sell",
      });

      await assignRole(accessDialog, salesRole, selectedBranches, true);
      await expect(accessDialog.getByRole("status")).toContainText(
        `Роль «${salesRole.name}» применена`,
      );
      expect(assignmentReplacements).toBe(2);
      await expectCurrentAccess(cashierApi, {
        branchAssignments: Object.fromEntries(
          selectedBranches.map((branch) => [branch.id, salesRole.id]),
        ),
        grantedPermission: "pos.sell",
        rejectedPermission: "reports.view",
      });

      await assignRole(accessDialog, reportsRole, [selectedBranches[1]!], true, true);
      await expect(accessDialog.getByRole("status")).toContainText(
        `Роль «${reportsRole.name}» применена`,
      );
      expect(assignmentReplacements).toBe(3);
      await expectCurrentAccess(cashierApi, {
        branchAssignments: { [selectedBranches[1]!.id]: reportsRole.id },
        grantedPermission: "reports.view",
        rejectedPermission: "pos.sell",
      });

      await accessDialog.getByText(/Журнал изменений доступа/).click();
      await expect(accessDialog.getByText("Отозвано").first()).toBeVisible();
      await expect(accessDialog.getByText("Восстановлено").first()).toBeVisible();
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

async function assignRole(
  dialog: Locator,
  role: TenantRole,
  branches: Branch[],
  replacesExisting: boolean,
  replaceAll = false,
): Promise<void> {
  await dialog.getByRole("button", { name: "Назначить роль" }).click();
  await dialog.getByLabel("Роль", { exact: true }).selectOption(role.id);
  await dialog.getByRole("button", { name: "В выбранных точках" }).click();
  for (const branch of branches) {
    await dialog.getByLabel(branch.name).check();
  }
  if (replaceAll) {
    const replaceAllSwitch = dialog.getByLabel("Оставить только выбранный доступ");
    await dialog.getByText("Оставить только выбранный доступ", { exact: true }).click();
    await expect(replaceAllSwitch).toBeChecked();
  }
  await dialog.getByRole("button", { name: "Проверить доступ" }).click();
  const confirmation = dialog.page().getByRole("dialog", {
    name: "Проверьте доступ сотрудника",
  });
  await expect(confirmation).toContainText(role.name);
  if (replacesExisting) {
    await expect(confirmation).toContainText("Роль будет заменена без разрыва доступа");
  }
  if (replaceAll) {
    await expect(confirmation).toContainText("Другой доступ будет отозван");
  }
  const response = dialog
    .page()
    .waitForResponse(
      (apiResponse) =>
        apiResponse.request().method() === "PUT" &&
        apiResponse.url().includes("/api/v1/users/") &&
        apiResponse.url().endsWith("/assignments") &&
        apiResponse.status() === 200,
    );
  await confirmation
    .getByRole("button", {
      name: replaceAll
        ? "Перевести и применить"
        : replacesExisting
          ? "Заменить и применить"
          : "Применить доступ",
    })
    .click();
  await response;
}

async function expectCurrentAccess(
  api: Awaited<ReturnType<typeof apiContext>>,
  expected: {
    branchAssignments: Record<string, string>;
    grantedPermission?: string;
    rejectedPermission: string;
  },
): Promise<void> {
  const response = await api.get("auth/me");
  expect(response.ok()).toBe(true);
  const currentUser = (await response.json()) as CurrentUser;
  if (expected.grantedPermission) {
    expect(currentUser.permissions).toContain(expected.grantedPermission);
  }
  expect(currentUser.permissions).not.toContain(expected.rejectedPermission);
  expect(currentUser.branch_assignments).toEqual(expected.branchAssignments);
}
