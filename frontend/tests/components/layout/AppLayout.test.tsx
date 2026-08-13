import { describe, expect, it } from "vitest";

import { buildNav } from "@/components/layout/nav";

const labels = (items: ReturnType<typeof buildNav>) => items.map((i) => i.label);
const SELLER_PERMS = [
  "catalog.view",
  "pos.shift_open",
  "pos.sell",
  "pos.shift_close",
  "sales.view.own",
  "audit.view.own",
] as const;
const OWNER_PERMS = [
  ...SELLER_PERMS,
  "reports.view",
  "branches.view",
  "registers.view",
  "users.view",
  "roles.create",
  "suppliers.view",
  "incoming.view",
  "batches.view",
  "settings.update",
] as const;
const TENANT_PLATFORM_ACCESS = ["platform.tenants.view"] as const;
const AUDIT_PLATFORM_ACCESS = ["platform.audit.global.view"] as const;

describe("buildNav — dashboard visibility", () => {
  it("hides «Главная» (dashboard) from a tenant user without reports.view (seller)", () => {
    const items = buildNav(false, true, false, SELLER_PERMS);
    expect(labels(items)).not.toContain("Главная");
    expect(items.some((i) => i.to === "/branches")).toBe(false);
    expect(items.some((i) => i.to === "/registers")).toBe(false);
    expect(items.some((i) => i.to === "/onboarding")).toBe(false);
    // The seller still gets the sections explicitly granted to the role.
    expect(items.some((i) => i.to === "/pos")).toBe(true);
    expect(items.some((i) => i.to === "/security")).toBe(true);
  });

  it("shows «Главная» to a tenant user with reports.view (owner)", () => {
    const items = buildNav(false, true, true, OWNER_PERMS);
    expect(labels(items)).toContain("Главная");
    expect(items[0]?.to).toBe("/");
  });

  it("shows «Главная» to a support user (admin/dev)", () => {
    const items = buildNav(true, false, false, [], false, false, TENANT_PLATFORM_ACCESS);
    expect(items[0]?.to).toBe("/");
    expect(items.some((i) => i.to === "/admin")).toBe(true);
    expect(items.some((i) => i.to === "/security")).toBe(true);
  });

  it("does not expose platform navigation without an active capability", () => {
    const items = buildNav(true, false, false, []);
    expect(items.some((item) => item.to === "/admin")).toBe(false);
    expect(items.some((item) => item.to === "/admin/tenants")).toBe(false);
  });

  it("shows global audit only to an unscoped developer", () => {
    const adminItems = buildNav(true, false, false, [], false, false, TENANT_PLATFORM_ACCESS);
    const developerItems = buildNav(true, false, false, [], true, false, AUDIT_PLATFORM_ACCESS);

    expect(adminItems.some((item) => item.to === "/audit")).toBe(false);
    expect(developerItems.some((item) => item.to === "/audit")).toBe(true);
  });
});

describe("buildNav — team management visibility", () => {
  it("hides «Пользователи»/«Роли» from a tenant user without users.view (seller)", () => {
    const items = buildNav(false, true, false, SELLER_PERMS);
    expect(labels(items)).not.toContain("Пользователи");
    expect(labels(items)).not.toContain("Роли");
    // The rest of the seller's workspace is untouched.
    expect(items.some((i) => i.to === "/catalog")).toBe(true);
    expect(items.some((i) => i.to === "/pos")).toBe(true);
  });

  it("shows team pages to an owner with their exact permissions", () => {
    const items = buildNav(false, true, true, OWNER_PERMS);
    expect(labels(items)).toContain("Пользователи");
    expect(labels(items)).toContain("Роли");
    expect(items.some((i) => i.to === "/users")).toBe(true);
    expect(items.some((i) => i.to === "/roles")).toBe(true);
  });

  it("does not use users.view as permission to open role management", () => {
    const userViewer = buildNav(false, true, false, ["users.view"]);
    expect(labels(userViewer)).toContain("Пользователи");
    expect(labels(userViewer)).not.toContain("Роли");

    const roleManager = buildNav(false, true, false, ["roles.update"]);
    expect(labels(roleManager)).not.toContain("Пользователи");
    expect(labels(roleManager)).not.toContain("Роли");

    const ownerRoleManager = buildNav(false, true, true, ["roles.update"]);
    expect(labels(ownerRoleManager)).toContain("Роли");
  });

  it("shows scoped support only the sections granted to the active context", () => {
    const userViewer = buildNav(true, true, false, ["users.view"], false, true);
    expect(userViewer.some((item) => item.to === "/roles")).toBe(false);
    expect(userViewer.some((item) => item.to === "/users")).toBe(true);

    const items = buildNav(true, true, false, ["users.view", "roles.update"], false, true);
    expect(items.some((item) => item.to === "/roles")).toBe(true);
    expect(items.some((item) => item.to === "/users")).toBe(true);
    expect(items.some((item) => item.to === "/catalog")).toBe(false);
    expect(items.some((item) => item.to === "/settings")).toBe(false);
  });
});

describe("buildNav — owner-only pages", () => {
  it("hides financial/reporting pages from a seller", () => {
    const items = buildNav(false, true, false, SELLER_PERMS);
    expect(labels(items)).not.toContain("Партии");
    expect(labels(items)).not.toContain("Тариф и оплата");
    expect(labels(items)).not.toContain("Отчёты");
    expect(labels(items)).not.toContain("Настройки");
  });

  it("shows financial/reporting pages to an owner", () => {
    const items = buildNav(false, true, true, OWNER_PERMS);
    expect(labels(items)).toContain("Партии");
    expect(labels(items)).toContain("Тариф и оплата");
    expect(labels(items)).toContain("Отчёты");
    expect(labels(items)).toContain("Настройки");
  });

  it("shows parties to any tenant role that has batches.view", () => {
    const items = buildNav(false, true, false, [...SELLER_PERMS, "batches.view"]);
    expect(labels(items)).toContain("Партии");
    expect(labels(items)).not.toContain("Тариф и оплата");
  });
});
