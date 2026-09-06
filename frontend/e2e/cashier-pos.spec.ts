import { request, test } from "@playwright/test";

import {
  addPosItemToCart,
  apiContext,
  apiLogin,
  CASHIER,
  catalogSearchKey,
  clearLoginRateLimit,
  completePosSale,
  expect,
  loginInBrowser,
  OWNER,
  payPosSaleCash,
  seedAcceptedBatch,
  seedBranch,
  seedCatalogItem,
  seedRegister,
  seedSupplier,
  uniqueName,
} from "./helpers";

interface RoleTemplate {
  name: string;
  slug: string;
  description: string | null;
  permissions: string[];
}

interface TenantRole {
  id: string;
}

interface UserAssignment {
  id: string;
}

interface TenantUser {
  id: string;
  email: string;
  assignments: UserAssignment[];
}

interface UserListResponse {
  items: TenantUser[];
}

test.describe("POS sale (branch-scoped cashier)", () => {
  test.beforeEach(() => {
    clearLoginRateLimit(OWNER.email);
    clearLoginRateLimit(CASHIER.email);
  });

  test("sees only the assigned register and completes a cash sale", async ({ page }) => {
    test.setTimeout(120_000);
    await page.setViewportSize({ width: 1600, height: 900 });

    const anonymousApi = await request.newContext();
    const ownerTokens = await apiLogin(anonymousApi, OWNER);
    const ownerApi = await apiContext(ownerTokens.access_token);

    const assignedBranch = await seedBranch(ownerApi, uniqueName("CASHIER-Branch"));
    const assignedRegister = await seedRegister(
      ownerApi,
      assignedBranch.id,
      uniqueName("CASHIER-Register"),
    );
    const hiddenBranch = await seedBranch(ownerApi, uniqueName("HIDDEN-Branch"));
    const hiddenRegister = await seedRegister(
      ownerApi,
      hiddenBranch.id,
      uniqueName("HIDDEN-Register"),
    );
    const supplier = await seedSupplier(ownerApi, uniqueName("CASHIER-Supplier"));
    const item = await seedCatalogItem(ownerApi, uniqueName("CASHIER-Med"), "12.50");
    await seedAcceptedBatch(ownerApi, {
      branchId: assignedBranch.id,
      supplierId: supplier.id,
      catalogId: item.id,
      qty: "10",
      purchasePrice: "8.00",
      salePrice: "12.50",
      expiresAt: isoDateInDays(180),
    });

    const templatesResponse = await ownerApi.get("templates");
    expect(templatesResponse.ok()).toBe(true);
    const templates = (await templatesResponse.json()) as RoleTemplate[];
    const cashierTemplate = templates.find((template) => template.slug === "cashier");
    expect(cashierTemplate).toBeDefined();

    const roleResponse = await ownerApi.post("roles", {
      data: {
        name: uniqueName("E2E Кассир"),
        description: cashierTemplate!.description,
        permissions: cashierTemplate!.permissions,
      },
    });
    expect(roleResponse.ok()).toBe(true);
    const cashierRole = (await roleResponse.json()) as TenantRole;

    const usersResponse = await ownerApi.get("users");
    expect(usersResponse.ok()).toBe(true);
    const users = (await usersResponse.json()) as UserListResponse;
    const cashier = users.items.find((user) => user.email === CASHIER.email);
    expect(cashier).toBeDefined();

    for (const assignment of cashier!.assignments) {
      const revokeResponse = await ownerApi.delete(
        `users/${cashier!.id}/assignments/${assignment.id}`,
      );
      expect(revokeResponse.ok()).toBe(true);
    }
    const assignmentResponse = await ownerApi.post(`users/${cashier!.id}/assignments`, {
      data: {
        role_id: cashierRole.id,
        branch_id: assignedBranch.id,
        password_required: true,
      },
    });
    expect(assignmentResponse.ok()).toBe(true);

    const cashierTokens = await apiLogin(anonymousApi, CASHIER);
    const cashierApi = await apiContext(cashierTokens.access_token);
    try {
      const registersResponse = await cashierApi.get("registers");
      expect(registersResponse.ok()).toBe(true);
      const registers = (await registersResponse.json()) as Array<{ id: string }>;
      expect(registers.map((register) => register.id)).toEqual([assignedRegister.id]);

      const hiddenRegistersResponse = await cashierApi.get(
        `registers?branch_id=${hiddenBranch.id}`,
      );
      expect(hiddenRegistersResponse.ok()).toBe(true);
      expect(await hiddenRegistersResponse.json()).toEqual([]);

      const forbiddenUsersResponse = await cashierApi.get("users");
      expect(forbiddenUsersResponse.status()).toBe(403);
    } finally {
      await cashierApi.dispose();
      await ownerApi.dispose();
      await anonymousApi.dispose();
    }

    await loginInBrowser(page, CASHIER);
    await page.goto("/pos");

    await expect(page.getByRole("link", { name: "Касса" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Пользователи" })).toHaveCount(0);
    await expect(page.getByRole("link", { name: "Роли" })).toHaveCount(0);
    await expect(page.getByRole("link", { name: "Тариф", exact: true })).toHaveCount(0);

    const registerField = page.getByLabel(/^Касса$/);
    await expect(registerField).toBeDisabled();
    await expect(registerField).toHaveValue(assignedRegister.name);
    await expect(page.getByText(hiddenRegister.name, { exact: true })).toHaveCount(0);

    await page.getByLabel("Наличные в кассе на начало смены").fill("100");
    await page.getByRole("button", { name: "Открыть смену" }).click();
    await expect(page.getByText("Смена открыта")).toBeVisible();

    await addPosItemToCart(page, {
      brandName: item.brand_name,
      qty: "1",
      expectedCartItems: 1,
      searchKey: catalogSearchKey(item.brand_name),
    });
    await payPosSaleCash(page, "12.50");
    await completePosSale(page);

    await page.getByRole("button", { name: /Печать чека/ }).click();
    const receipt = page.locator(".receipt-print");
    await expect(receipt.getByText("КАССОВЫЙ ЧЕК")).toBeVisible();
    await expect(receipt.getByText(new RegExp(item.brand_name))).toBeVisible();
    await expect(receipt.getByText(/12\.50/).first()).toBeVisible();
  });
});

function isoDateInDays(days: number): string {
  const value = new Date();
  value.setDate(value.getDate() + days);
  return value.toISOString().slice(0, 10);
}
