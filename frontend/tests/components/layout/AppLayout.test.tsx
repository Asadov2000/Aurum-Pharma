import { describe, expect, it } from "vitest";

import { buildNav } from "@/components/layout/nav";

const labels = (items: ReturnType<typeof buildNav>) => items.map((i) => i.label);

describe("buildNav — dashboard visibility", () => {
  it("hides «Главная» (dashboard) from a tenant user without reports.view (seller)", () => {
    const items = buildNav(false, true, /* canSeeDashboard */ false, /* canManageTeam */ false);
    expect(labels(items)).not.toContain("Главная");
    // …but the seller still gets the tenant workspace items.
    expect(items.some((i) => i.to === "/pos")).toBe(true);
  });

  it("shows «Главная» to a tenant user with reports.view (owner)", () => {
    const items = buildNav(false, true, /* canSeeDashboard */ true, /* canManageTeam */ true);
    expect(labels(items)).toContain("Главная");
    expect(items[0]?.to).toBe("/");
  });

  it("shows «Главная» to a support user (admin/dev)", () => {
    const items = buildNav(true, false, /* canSeeDashboard */ true, /* canManageTeam */ false);
    expect(items[0]?.to).toBe("/");
    expect(items.some((i) => i.to === "/admin/tenants")).toBe(true);
  });
});

describe("buildNav — team management visibility", () => {
  it("hides «Пользователи»/«Роли» from a tenant user without users.view (seller)", () => {
    const items = buildNav(false, true, /* canSeeDashboard */ false, /* canManageTeam */ false);
    expect(labels(items)).not.toContain("Пользователи");
    expect(labels(items)).not.toContain("Роли");
    // The rest of the seller's workspace is untouched.
    expect(items.some((i) => i.to === "/catalog")).toBe(true);
    expect(items.some((i) => i.to === "/pos")).toBe(true);
  });

  it("shows «Пользователи»/«Роли» to a tenant user with users.view (owner)", () => {
    const items = buildNav(false, true, /* canSeeDashboard */ true, /* canManageTeam */ true);
    expect(labels(items)).toContain("Пользователи");
    expect(labels(items)).toContain("Роли");
    expect(items.some((i) => i.to === "/users")).toBe(true);
    expect(items.some((i) => i.to === "/roles")).toBe(true);
  });
});
