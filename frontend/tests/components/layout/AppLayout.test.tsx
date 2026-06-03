import { describe, expect, it } from "vitest";

import { buildNav } from "@/components/layout/nav";

const labels = (items: ReturnType<typeof buildNav>) => items.map((i) => i.label);

describe("buildNav — dashboard visibility", () => {
  it("hides «Главная» (dashboard) from a tenant user without reports.view (seller)", () => {
    const items = buildNav(false, true, /* canSeeDashboard */ false);
    expect(labels(items)).not.toContain("Главная");
    // …but the seller still gets the tenant workspace items.
    expect(items.some((i) => i.to === "/pos")).toBe(true);
  });

  it("shows «Главная» to a tenant user with reports.view (owner)", () => {
    const items = buildNav(false, true, /* canSeeDashboard */ true);
    expect(labels(items)).toContain("Главная");
    expect(items[0]?.to).toBe("/");
  });

  it("shows «Главная» to a support user (admin/dev)", () => {
    const items = buildNav(true, false, /* canSeeDashboard */ true);
    expect(items[0]?.to).toBe("/");
    expect(items.some((i) => i.to === "/admin/tenants")).toBe(true);
  });
});
