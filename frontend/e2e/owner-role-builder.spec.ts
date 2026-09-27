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

// Authentication cookies must not enter browser artifacts.
test.use({ trace: "off", screenshot: "off", video: "off" });

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
      await page
        .locator("header")
        .getByRole("button", { name: "Создать роль", exact: true })
        .click();
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

  test("reviews permission changes, restores removals and discards an unpublished role edit", async ({
    page,
  }) => {
    const anonymousApi = await request.newContext();
    const ownerTokens = await apiLogin(anonymousApi, OWNER);
    const ownerApi = await apiContext(ownerTokens.access_token);
    try {
      const permissionsResponse = await ownerApi.get("permissions");
      expect(permissionsResponse.ok()).toBe(true);
      const permissions = (await permissionsResponse.json()) as DelegablePermission[];
      const safePermissions = permissions.filter(
        (permission) =>
          permission.is_active &&
          permission.target_role_type === "tenant" &&
          permission.scope_type !== "PLATFORM" &&
          permission.risk_level === "normal" &&
          !permission.requires_step_up &&
          !permission.requires_confirmation,
      );
      expect(safePermissions.length).toBeGreaterThan(1);
      const initialPermission =
        safePermissions.find((permission) => permission.code === "catalog.view") ??
        safePermissions[0]!;
      const addedPermission = safePermissions.find(
        (permission) => permission.code !== initialPermission.code,
      )!;
      await page.setViewportSize({ width: 390, height: 844 });
      await page.emulateMedia({ reducedMotion: "reduce" });
      await loginInBrowser(page, OWNER);
      await page.goto("/roles");
      await page.getByRole("button", { name: "Вид интерфейса" }).click();
      await Promise.all([
        page.waitForResponse(
          (response) =>
            new URL(response.url()).pathname.endsWith("/auth/preferences") &&
            response.request().method() === "PATCH" &&
            response.ok() &&
            Boolean(response.request().postData()?.includes('"density":"touch"')),
        ),
        page.getByRole("button", { name: "Сенсор", exact: true }).click(),
      ]);
      await page.keyboard.press("Escape");
      await expect(page.locator("html")).toHaveAttribute("data-density", "touch");
      const roleName = uniqueName("Проверка изменений");
      await page
        .locator("header")
        .getByRole("button", { name: "Создать роль", exact: true })
        .click();
      let dialog = page.getByRole("dialog", { name: "Создать роль", exact: true });
      const permissionControl = (permission: DelegablePermission) =>
        dialog.locator("label").filter({ hasText: permission.name }).getByRole("checkbox");
      await dialog.getByLabel("Название", { exact: true }).fill(roleName);
      await dialog.getByRole("button", { name: /^Права доступа · / }).click();
      await dialog.getByPlaceholder("Найти функцию").fill(initialPermission.code);
      await permissionControl(initialPermission).check();
      const createResponse = page.waitForResponse(
        (response) =>
          new URL(response.url()).pathname.endsWith("/roles") &&
          response.request().method() === "POST" &&
          response.ok(),
      );
      await dialog.getByRole("button", { name: "Создать роль", exact: true }).click();
      const createdRole = (await (await createResponse).json()) as TenantRole;
      expect(createdRole.permissions).toEqual([initialPermission.code]);
      expect(createdRole.version).toBe(1);
      await page.getByLabel("Поиск", { exact: true }).fill(roleName);
      const roleCard = page
        .getByRole("heading", { name: roleName, exact: true })
        .locator("xpath=ancestor::article");
      const editButton = roleCard.getByRole("button", { name: "Изменить", exact: true });
      await editButton.click();
      dialog = page.getByRole("dialog", { name: "Изменить роль", exact: true });
      await expect(
        dialog.getByText("Активных назначений", { exact: true }).locator(".."),
      ).toContainText(/Активных назначений\s*0/);
      await expect(
        dialog.getByRole("button", { name: "Проверить и опубликовать", exact: true }),
      ).toBeDisabled();
      await dialog.getByRole("button", { name: /^Права доступа · / }).click();
      const functionSearch = dialog.getByPlaceholder("Найти функцию");
      await functionSearch.fill(addedPermission.code);
      await permissionControl(addedPermission).check();
      await functionSearch.fill(initialPermission.code);
      await permissionControl(initialPermission).uncheck();
      await functionSearch.clear();
      await dialog.getByRole("button", { name: "Изменения · 2", exact: true }).click();
      await expect(dialog.getByRole("checkbox")).toHaveCount(2);
      await expect(dialog.getByText("Добавлено", { exact: true })).toHaveCount(1);
      await expect(dialog.getByText("Убрано", { exact: true })).toHaveCount(1);
      await dialog
        .locator("label")
        .filter({ hasText: initialPermission.name })
        .getByRole("checkbox")
        .click();
      await expect(
        dialog.getByRole("button", { name: "Изменения · 1", exact: true }),
      ).toHaveAttribute("aria-pressed", "true");
      await expect(dialog.getByRole("checkbox")).toHaveCount(1);
      await expect(dialog.getByText("Убрано", { exact: true })).toHaveCount(0);
      await dialog.getByRole("button", { name: "Отмена", exact: true }).click();
      let discardDialog = page.getByRole("dialog", { name: "Отменить изменения?" });
      await discardDialog
        .getByRole("button", { name: "Продолжить редактирование", exact: true })
        .click();
      await expect(
        dialog.getByRole("button", { name: "Изменения · 1", exact: true }),
      ).toHaveAttribute("aria-pressed", "true");
      await dialog.getByRole("button", { name: "Отмена", exact: true }).click();
      discardDialog = page.getByRole("dialog", { name: "Отменить изменения?" });
      await discardDialog
        .getByRole("button", { name: "Выйти без сохранения", exact: true })
        .click();
      await expect(dialog).toBeHidden();
      await expect(editButton).toBeFocused();
      const unchangedResponse = await ownerApi.get("roles");
      expect(unchangedResponse.ok()).toBe(true);
      const unchangedRoles = (await unchangedResponse.json()) as TenantRole[];
      expect(unchangedRoles.find((role) => role.id === createdRole.id)).toMatchObject({
        permissions: [initialPermission.code],
        version: 1,
      });
      await editButton.click();
      dialog = page.getByRole("dialog", { name: "Изменить роль", exact: true });
      await dialog.getByRole("button", { name: /^Права доступа · / }).click();
      await dialog.getByPlaceholder("Найти функцию").fill(addedPermission.code);
      await permissionControl(addedPermission).check();
      await dialog.getByRole("button", { name: "Проверить и опубликовать", exact: true }).click();
      const publicationDialog = page.getByRole("dialog", { name: "Опубликовать версию 2?" });
      await expect(publicationDialog).toContainText("Затронуто активных назначений: 0");
      const updateResponse = page.waitForResponse(
        (response) =>
          new URL(response.url()).pathname.endsWith(`/roles/${createdRole.id}`) &&
          response.request().method() === "PATCH" &&
          response.ok(),
      );
      await publicationDialog
        .getByRole("button", { name: "Опубликовать версию", exact: true })
        .click();
      const response = await updateResponse;
      const payload = response.request().postDataJSON() as {
        expected_version: number;
        permissions: string[];
      };
      expect(payload.expected_version).toBe(1);
      expect(new Set(payload.permissions)).toEqual(
        new Set([initialPermission.code, addedPermission.code]),
      );
      const updatedRole = (await response.json()) as TenantRole;
      expect(updatedRole.version).toBe(2);
      await expect(roleCard).toContainText("Версия 2");
    } finally {
      await ownerApi.dispose();
      await anonymousApi.dispose();
    }
  });
});
