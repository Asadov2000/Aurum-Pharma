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
  "users.view",
  "roles.create",
  "suppliers.view",
  "incoming.view",
  "settings.update",
] as const;

describe("buildNav — dashboard visibility", () => {
  it("hides «Главная» (dashboard) from a tenant user without reports.view (seller)", () => {
    const items = buildNav(false, true, SELLER_PERMS);
    expect(labels(items)).not.toContain("Главная");
    // …but the seller still gets the tenant workspace items.
    expect(items.some((i) => i.to === "/pos")).toBe(true);
  });

  it("shows «Главная» to a tenant user with reports.view (owner)", () => {
    const items = buildNav(false, true, OWNER_PERMS);
    expect(labels(items)).toContain("Главная");
    expect(items[0]?.to).toBe("/");
  });

  it("shows «Главная» to a support user (admin/dev)", () => {
    const items = buildNav(true, false, []);
    expect(items[0]?.to).toBe("/");
    expect(items.some((i) => i.to === "/admin/tenants")).toBe(true);
  });
});

describe("buildNav — team management visibility", () => {
  it("hides «Пользователи»/«Роли» from a tenant user without users.view (seller)", () => {
    const items = buildNav(false, true, SELLER_PERMS);
    expect(labels(items)).not.toContain("Пользователи");
    expect(labels(items)).not.toContain("Роли");
    // The rest of the seller's workspace is untouched.
    expect(items.some((i) => i.to === "/catalog")).toBe(true);
    expect(items.some((i) => i.to === "/pos")).toBe(true);
  });

  it("shows team pages to an owner with their exact permissions", () => {
    const items = buildNav(false, true, OWNER_PERMS);
    expect(labels(items)).toContain("Пользователи");
    expect(labels(items)).toContain("Роли");
    expect(items.some((i) => i.to === "/users")).toBe(true);
    expect(items.some((i) => i.to === "/roles")).toBe(true);
  });

  it("does not use users.view as permission to open role management", () => {
    const userViewer = buildNav(false, true, ["users.view"]);
    expect(labels(userViewer)).toContain("Пользователи");
    expect(labels(userViewer)).not.toContain("Роли");

    const roleManager = buildNav(false, true, ["roles.update"]);
    expect(labels(roleManager)).not.toContain("Пользователи");
    expect(labels(roleManager)).toContain("Роли");
  });
});

describe("buildNav — owner-only pages", () => {
  it("hides financial/reporting pages from a seller", () => {
    const items = buildNav(false, true, SELLER_PERMS);
    expect(labels(items)).not.toContain("Партии");
    expect(labels(items)).not.toContain("Биллинг");
    expect(labels(items)).not.toContain("Отчёты");
    expect(labels(items)).not.toContain("Настройки");
  });

  it("shows financial/reporting pages to an owner", () => {
    const items = buildNav(false, true, OWNER_PERMS);
    expect(labels(items)).toContain("Партии");
    expect(labels(items)).toContain("Биллинг");
    expect(labels(items)).toContain("Отчёты");
    expect(labels(items)).toContain("Настройки");
  });
});
