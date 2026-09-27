import { expect, request, test, type Page, type Response } from "@playwright/test";

import {
  apiContext,
  apiLogin,
  CASHIER,
  loginInBrowser,
  OWNER,
  seedBranch,
  uniqueName,
} from "./helpers";

// Account contacts and authentication cookies must not enter browser artifacts.
test.use({ trace: "off", screenshot: "off", video: "off" });

interface ProfileResponse {
  id: string;
  full_name: string;
  is_tenant_owner: boolean;
  platform_role: "developer" | "administrator" | null;
  access_assignments: {
    role_id: string;
    role_name: string;
    scope: "tenant" | "branch";
    branch: { id: string; name: string } | null;
  }[];
}

interface SelfIdentity {
  id: string;
  permissions: string[];
}

async function seedCashierProfileAccess() {
  const anonymous = await request.newContext();
  let ownerApi: Awaited<ReturnType<typeof apiContext>> | null = null;
  try {
    ownerApi = await apiContext((await apiLogin(anonymous, OWNER)).access_token);
    const branch = await seedBranch(ownerApi, uniqueName("Profile branch"));
    const roleResponse = await ownerApi.post("roles", {
      data: {
        name: uniqueName("Profile cashier"),
        description: null,
        permissions: ["pos.sell"],
      },
    });
    expect(roleResponse.status()).toBe(201);
    const role = (await roleResponse.json()) as { id: string; name: string };
    const usersResponse = await ownerApi.get("users");
    expect(usersResponse.status()).toBe(200);
    const users = (await usersResponse.json()) as { items: { id: string; email: string }[] };
    const cashier = users.items.find((user) => user.email === CASHIER.email);
    if (!cashier) throw new Error("Cashier fixture is unavailable");
    const assigned = await ownerApi.post(`users/${cashier.id}/assignments`, {
      data: { role_id: role.id, branch_id: branch.id, password_required: true },
    });
    expect(assigned.status()).toBe(201);
    return { role, branch };
  } finally {
    await ownerApi?.dispose();
    await anonymous.dispose();
  }
}

function waitForSelfResponse(page: Page, path: string): Promise<Response> {
  return page.waitForResponse(
    (response) =>
      response.request().method() === "GET" && new URL(response.url()).pathname === path,
  );
}

function trackManagementRequests(page: Page): () => number {
  let count = 0;
  page.on("request", (request) => {
    if (/^\/api\/v1\/(?:users|roles)(?:\/|$)/.test(new URL(request.url()).pathname)) count += 1;
  });
  return () => count;
}

async function expectPersonalProfile(page: Page, response: Response, selfId: string) {
  expect(response.status()).toBe(200);
  const profile = (await response.json()) as ProfileResponse;
  expect(profile.id).toBe(selfId);
  const main = page.getByRole("main");
  await expect(main.getByRole("heading", { name: "Мой профиль", exact: true })).toBeVisible();
  await expect(main.getByRole("heading", { name: "Личные данные", exact: true })).toBeVisible();
  await expect(main.getByRole("heading", { name: "Рабочий доступ", exact: true })).toBeVisible();
  await expect(main.getByRole("heading", { name: "Способы входа", exact: true })).toBeVisible();
  // Use counts rather than locator failure output to avoid printing account data.
  await expect
    .poll(() =>
      main.getByRole("heading", { name: profile.full_name, exact: true, level: 2 }).count(),
    )
    .toBe(1);
  await expect
    .poll(() => main.locator("dl").getByText(profile.full_name, { exact: true }).count())
    .toBe(1);
  await expect(main.getByRole("textbox", { name: /^(?:Имя|ФИО|Полное имя)$/ })).toHaveCount(0);

  for (const [name, href] of [
    ["Безопасность аккаунта", "/security"],
    ["Открыть безопасность", "/security"],
    ["Настройки интерфейса", "/settings"],
    ["Уведомления", "/notifications"],
  ] as const) {
    await expect(main.getByRole("link", { name, exact: true })).toHaveAttribute("href", href);
  }
  await expect(main.locator('a[href="/users"], a[href="/roles"]')).toHaveCount(0);
  for (const assignment of profile.access_assignments) {
    await expect(main.getByText(assignment.role_name, { exact: true }).first()).toBeVisible();
    const scope = assignment.scope === "tenant" ? "Все точки аптеки" : assignment.branch?.name;
    if (scope) await expect(main.getByText(scope, { exact: true }).first()).toBeVisible();
  }
  return profile;
}

test.describe("Personal profile", () => {
  test("cashier opens their own readonly profile from the mobile avatar without role-management access", async ({
    page,
  }) => {
    const assigned = await seedCashierProfileAccess();
    await page.setViewportSize({ width: 390, height: 844 });
    await loginInBrowser(page, CASHIER);
    const meResponse = waitForSelfResponse(page, "/api/v1/auth/me");
    await page.goto("/pos", { waitUntil: "domcontentloaded" });
    const me = await meResponse;
    expect(me.status()).toBe(200);
    const identity = (await me.json()) as SelfIdentity;
    expect(
      identity.permissions.some(
        (permission) => permission.startsWith("users.") || permission.startsWith("roles."),
      ),
    ).toBe(false);

    const managementRequests = trackManagementRequests(page);
    const profileResponse = waitForSelfResponse(page, "/api/v1/auth/profile");
    await page.getByRole("link", { name: "Открыть мой профиль", exact: true }).click();
    await expect(page).toHaveURL(/\/profile(?:\?|$)/);
    const profile = await expectPersonalProfile(page, await profileResponse, identity.id);
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    ).toBe(true);
    expect(profile.is_tenant_owner).toBe(false);
    expect(profile.platform_role).toBeNull();
    expect(profile.access_assignments.length).toBeGreaterThan(0);
    expect(
      profile.access_assignments.some(
        (assignment) =>
          assignment.role_id === assigned.role.id &&
          assignment.role_name === assigned.role.name &&
          assignment.scope === "branch" &&
          assignment.branch?.id === assigned.branch.id &&
          assignment.branch.name === assigned.branch.name,
      ),
    ).toBe(true);
    await expect(page.getByRole("link", { name: "Пользователи", exact: true })).toHaveCount(0);
    await expect(page.getByRole("link", { name: "Роли", exact: true })).toHaveCount(0);
    expect(managementRequests()).toBe(0);
  });

  test("owner loads their own profile directly and retains it after reload", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await loginInBrowser(page, OWNER);
    const managementRequests = trackManagementRequests(page);
    const meResponse = waitForSelfResponse(page, "/api/v1/auth/me");
    const profileResponse = waitForSelfResponse(page, "/api/v1/auth/profile");
    await page.goto("/profile", { waitUntil: "domcontentloaded" });
    const me = await meResponse;
    expect(me.status()).toBe(200);
    const identity = (await me.json()) as SelfIdentity;
    const profile = await expectPersonalProfile(page, await profileResponse, identity.id);
    expect(profile.is_tenant_owner).toBe(true);
    expect(profile.platform_role).toBeNull();
    await expect(
      page.getByRole("link", { name: "Открыть мой профиль", exact: true }),
    ).toHaveAttribute("href", "/profile");

    const reloadedProfile = waitForSelfResponse(page, "/api/v1/auth/profile");
    await page.reload({ waitUntil: "domcontentloaded" });
    await expectPersonalProfile(page, await reloadedProfile, identity.id);
    expect(managementRequests()).toBe(0);
  });
});
