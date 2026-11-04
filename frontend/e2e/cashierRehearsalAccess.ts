import { request, type APIRequestContext } from "@playwright/test";

import { apiContext, apiLogin, expect, seedBranch, seedRegister, uniqueName } from "./helpers";
import { type RehearsalFixture } from "./onlineCashRehearsalFixture";

interface CashierIdentity {
  id: string;
  is_tenant_owner: boolean;
  is_developer: boolean;
  is_administrator: boolean;
  level: number;
  permissions: string[];
  permission_scopes: Record<string, string[] | null>;
  branch_assignments: Record<string, string>;
  platform_capabilities: string[];
}

export async function createScopedRehearsalCashier(
  ownerApi: APIRequestContext,
  fixture: RehearsalFixture,
) {
  const templatesResponse = await ownerApi.get("templates");
  expect(templatesResponse.ok()).toBe(true);
  const templates = (await templatesResponse.json()) as {
    slug: string;
    description: string | null;
    permissions: string[];
  }[];
  const template = templates.find((item) => item.slug === "cashier");
  if (!template) throw new Error("The standard cashier template is missing");
  const roleResponse = await ownerApi.post("roles", {
    data: {
      name: uniqueName("Scoped physical cashier"),
      description: template.description,
      permissions: template.permissions,
    },
  });
  expect(roleResponse.ok()).toBe(true);
  const role = (await roleResponse.json()) as { id: string; permissions: string[] };
  expect(
    JSON.stringify([...role.permissions].sort()) ===
      JSON.stringify([...template.permissions].sort()),
  ).toBe(true);
  const suffix = uniqueName("physical-cashier");
  const credentials = { email: `${suffix}@e2e.aurum.tj`, password: "" };
  const invited = await ownerApi.post("users/invite", {
    data: {
      operation_id: crypto.randomUUID(),
      email: credentials.email,
      full_name: "Учебный кассир поштучных продаж",
      role_id: role.id,
      branch_id: fixture.branch.id,
      password_required: false,
    },
  });
  expect(invited.status()).toBe(201);
  const assignment = (await invited.json()) as { id: string; user_id: string };
  const login = await request.newContext();
  let cashierApi: APIRequestContext | undefined;
  const cleanup = async () => {
    // This account and assignment belong only to this fixture. Never revoke the
    // seeded CASHIER's assignments, which another E2E may have prepared.
    const revoked = await ownerApi.delete(
      `users/${assignment.user_id}/assignments/${assignment.id}`,
    );
    expect(revoked.ok()).toBe(true);
    await cashierApi?.dispose();
    await login.dispose();
  };
  try {
    const tokens = await apiLogin(login, credentials);
    cashierApi = await apiContext(tokens.access_token);
    const meResponse = await cashierApi.get("auth/me");
    expect(meResponse.ok()).toBe(true);
    const me = (await meResponse.json()) as CashierIdentity;
    expect(!me.is_tenant_owner && !me.is_developer && !me.is_administrator && me.level === 4).toBe(
      true,
    );
    expect(me.platform_capabilities).toEqual([]);
    expect(Object.keys(me.branch_assignments)).toEqual([fixture.branch.id]);
    expect(me.branch_assignments[fixture.branch.id] === role.id).toBe(true);
    // Tenant-only catalog.view is intentionally not granted by a branch assignment.
    expect(template.permissions).toHaveLength(9);
    expect(me.permissions).toHaveLength(8);
    expect(template.permissions.filter((code) => !me.permissions.includes(code))).toEqual([
      "catalog.view",
    ]);
    expect(me.permissions.filter((code) => !template.permissions.includes(code))).toEqual([]);
    for (const permission of ["pos.sell", "pos.shift_open", "pos.shift_close", "pos.refund"]) {
      expect(me.permission_scopes[permission]).toEqual([fixture.branch.id]);
    }
    const hiddenBranch = await seedBranch(ownerApi, uniqueName("Hidden physical branch"));
    const hiddenRegister = await seedRegister(
      ownerApi,
      hiddenBranch.id,
      uniqueName("Hidden physical register"),
    );
    const registersResponse = await cashierApi.get("registers");
    expect(registersResponse.ok()).toBe(true);
    const registers = (await registersResponse.json()) as { id: string }[];
    expect(registers.length === 1 && registers[0].id === fixture.register.id).toBe(true);
    const hidden = await cashierApi.get("registers", { params: { branch_id: hiddenBranch.id } });
    expect(hidden.ok()).toBe(true);
    expect(await hidden.json()).toEqual([]);
    expect((await cashierApi.get("users")).status()).toBe(403);
    expect((await cashierApi.get(`catalog/${fixture.medicine.id}`)).status()).toBe(403);
    return { api: cashierApi, credentials, userId: me.id, hiddenBranch, hiddenRegister, cleanup };
  } catch (error) {
    await cleanup();
    throw error;
  }
}
