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

interface DelegablePermission {
  code: string;
  is_active: boolean;
  name: string;
  requires_confirmation: boolean;
  requires_step_up: boolean;
  risk_level: "normal" | "sensitive" | "critical";
  scope_type: "PLATFORM" | "TENANT_ALL" | "BRANCH_SET" | "OWN";
  target_role_type: "platform" | "tenant";
}

interface TenantRole {
  id: string;
  name: string;
  permissions: string[];
  version: number;
}

const PROTECTED_GOVERNANCE_CODES = [
  "roles.assign",
  "roles.create",
  "roles.update",
  "users.block",
  "users.delete",
  "users.invite",
  "users.update",
] as const;

test.describe("Owner role builder", () => {
  test.beforeEach(() => {
    clearLoginRateLimit(OWNER.email);
  });

  test("publishes only delegable permissions and rejects a forged privilege", async ({ page }) => {
    const anonymousApi = await request.newContext();
    const ownerTokens = await apiLogin(anonymousApi, OWNER);
    const ownerApi = await apiContext(ownerTokens.access_token);

    try {
      const permissionsResponse = await ownerApi.get("permissions");
      expect(permissionsResponse.ok()).toBe(true);
      const permissions = (await permissionsResponse.json()) as DelegablePermission[];
      const permissionCodes = new Set(permissions.map((permission) => permission.code));

      expect(permissions.length).toBeGreaterThan(1);
      expect(
        permissions.every(
          (permission) =>
            permission.is_active &&
            permission.target_role_type === "tenant" &&
            permission.scope_type !== "PLATFORM",
        ),
      ).toBe(true);
      for (const code of PROTECTED_GOVERNANCE_CODES) {
        expect(permissionCodes.has(code)).toBe(false);
      }

      const safePermissions = permissions.filter(
        (permission) =>
          permission.risk_level === "normal" &&
          !permission.requires_step_up &&
          !permission.requires_confirmation,
      );
      expect(safePermissions.length).toBeGreaterThan(1);
      const initialPermission =
        safePermissions.find((permission) => permission.code === "catalog.view") ??
        safePermissions[0]!;
      const addedPermission =
        safePermissions.find(
          (permission) =>
            permission.code === "pos.sell" && permission.code !== initialPermission.code,
        ) ?? safePermissions.find((permission) => permission.code !== initialPermission.code)!;

      const rejectedName = uniqueName("Недопустимая роль");
      const rejectedResponse = await ownerApi.post("roles", {
        data: {
          name: rejectedName,
          description: null,
          permissions: ["roles.assign"],
        },
      });
      expect(rejectedResponse.status()).toBe(403);

      await loginInBrowser(page, OWNER);
      await page.goto("/roles");

      const roleName = uniqueName("Контролёр доступа");
      await page.getByRole("button", { name: "Создать роль" }).click();
      let dialog = page.getByRole("dialog", { name: "Создать роль" });
      await dialog.getByRole("textbox", { name: "Название", exact: true }).fill(roleName);
      await dialog.getByLabel("Описание (необязательно)").fill("Безопасная тестовая роль");
      await dialog.getByPlaceholder("Найти функцию").fill(initialPermission.code);
      await dialog.getByRole("checkbox").check();

      const createResponse = page.waitForResponse(
        (response) =>
          response.url().endsWith("/api/v1/roles") &&
          response.request().method() === "POST" &&
          response.ok(),
      );
      await dialog.getByRole("button", { name: "Создать роль" }).click();
      const createdRole = (await (await createResponse).json()) as TenantRole;
      expect(createdRole.permissions).toEqual([initialPermission.code]);
      expect(createdRole.version).toBe(1);

      const roleSearch = page.getByLabel("Поиск");
      await roleSearch.fill(roleName);
      let roleCard = page
        .getByRole("heading", { name: roleName, exact: true })
        .locator("xpath=ancestor::article");
      await expect(roleCard).toContainText("Версия 1");
      await expect(roleCard).toContainText("1 функция");

      await roleCard.getByRole("button", { name: "Изменить" }).click();
      dialog = page.getByRole("dialog", { name: "Изменить роль" });
      await dialog.getByPlaceholder("Найти функцию").fill(addedPermission.code);
      await dialog.getByRole("checkbox").check();
      await dialog.getByRole("button", { name: "Проверить и опубликовать" }).click();

      const publicationDialog = page.getByRole("dialog", {
        name: "Опубликовать версию 2?",
      });
      const updateResponse = page.waitForResponse(
        (response) =>
          response.url().endsWith(`/api/v1/roles/${createdRole.id}`) &&
          response.request().method() === "PATCH" &&
          response.ok(),
      );
      await publicationDialog.getByRole("button", { name: "Опубликовать версию" }).click();
      const updatedRole = (await (await updateResponse).json()) as TenantRole;
      expect(updatedRole.version).toBe(2);
      expect(new Set(updatedRole.permissions)).toEqual(
        new Set([initialPermission.code, addedPermission.code]),
      );

      roleCard = page
        .getByRole("heading", { name: roleName, exact: true })
        .locator("xpath=ancestor::article");
      await expect(roleCard).toContainText("Версия 2");
      await expect(roleCard).toContainText("2 функции");
      await roleCard.getByRole("button", { name: "История" }).click();
      const historyDialog = page.getByRole("dialog", {
        name: `История роли «${roleName}»`,
      });
      const versionTwo = historyDialog.getByRole("listitem").filter({ hasText: "Версия 2" });
      const versionOne = historyDialog.getByRole("listitem").filter({ hasText: "Версия 1" });
      await expect(versionTwo).toContainText(addedPermission.name);
      await expect(versionOne).toContainText(initialPermission.name);

      const rolesResponse = await ownerApi.get("roles");
      expect(rolesResponse.ok()).toBe(true);
      const roles = (await rolesResponse.json()) as TenantRole[];
      expect(roles.find((role) => role.name === rejectedName)).toBeUndefined();
      expect(roles.find((role) => role.id === createdRole.id)).toMatchObject({
        name: roleName,
        version: 2,
      });
    } finally {
      await ownerApi.dispose();
      await anonymousApi.dispose();
    }
  });
});
