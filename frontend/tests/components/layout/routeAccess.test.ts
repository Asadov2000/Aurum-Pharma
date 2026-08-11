import { describe, expect, it } from "vitest";

import {
  canAccessPath,
  firstAccessiblePath,
  type RouteAccessContext,
} from "@/components/layout/routeAccess";

const SELLER: RouteAccessContext = {
  isDeveloper: false,
  isAdministrator: false,
  isSupportScoped: false,
  isTenantOwner: false,
  hasTenant: true,
  permissions: ["catalog.view", "pos.shift_open", "pos.sell", "sales.view.own", "audit.view.own"],
  platformCapabilities: [],
};

describe("route access", () => {
  it("allows only explicitly granted seller sections", () => {
    expect(canAccessPath("/pos", SELLER)).toBe(true);
    expect(canAccessPath("/catalog", SELLER)).toBe(true);
    expect(canAccessPath("/sales", SELLER)).toBe(true);
    expect(canAccessPath("/audit", SELLER)).toBe(true);
    expect(canAccessPath("/security", SELLER)).toBe(true);
    expect(canAccessPath("/branches", SELLER)).toBe(false);
    expect(canAccessPath("/registers", SELLER)).toBe(false);
    expect(canAccessPath("/settings", SELLER)).toBe(false);
    expect(canAccessPath("/incoming/document-1", SELLER)).toBe(false);
  });

  it("uses the POS as the primary fallback for a cashier", () => {
    expect(firstAccessiblePath(SELLER)).toBe("/pos");
  });

  it("keeps an unscoped support account outside tenant routes", () => {
    const support: RouteAccessContext = {
      isDeveloper: false,
      isAdministrator: true,
      isSupportScoped: false,
      isTenantOwner: false,
      hasTenant: false,
      permissions: [],
      platformCapabilities: ["platform.tenants.view"],
    };

    expect(canAccessPath("/", support)).toBe(true);
    expect(canAccessPath("/admin", support)).toBe(true);
    expect(canAccessPath("/admin/tenants", support)).toBe(true);
    expect(canAccessPath("/notifications", support)).toBe(true);
    expect(canAccessPath("/security", support)).toBe(true);
    expect(canAccessPath("/catalog", support)).toBe(false);
    expect(canAccessPath("/roles", support)).toBe(false);
  });

  it("allows global audit only to an unscoped developer", () => {
    const developer: RouteAccessContext = {
      isDeveloper: true,
      isAdministrator: false,
      isSupportScoped: false,
      isTenantOwner: false,
      hasTenant: false,
      permissions: [],
      platformCapabilities: ["platform.audit.global.view"],
    };
    const administrator: RouteAccessContext = {
      ...developer,
      isDeveloper: false,
      isAdministrator: true,
      platformCapabilities: [],
    };

    expect(canAccessPath("/audit", developer)).toBe(true);
    expect(canAccessPath("/audit", administrator)).toBe(false);
  });

  it("allows the filtered role catalogue in an explicit support tenant context", () => {
    const scopedSupport: RouteAccessContext = {
      isDeveloper: false,
      isAdministrator: true,
      isSupportScoped: true,
      isTenantOwner: false,
      hasTenant: true,
      permissions: ["users.view", "roles.update"],
      platformCapabilities: ["platform.tenants.view"],
    };

    expect(canAccessPath("/roles", scopedSupport)).toBe(true);
    expect(canAccessPath("/users", scopedSupport)).toBe(true);
    expect(canAccessPath("/", scopedSupport)).toBe(false);
    expect(canAccessPath("/catalog", scopedSupport)).toBe(false);
    expect(
      canAccessPath("/roles", {
        ...scopedSupport,
        permissions: ["users.view"],
      }),
    ).toBe(false);
  });

  it("leaves unknown paths to the router", () => {
    expect(canAccessPath("/future-section", SELLER)).toBe(true);
  });

  it("denies unknown paths inside a scoped support context", () => {
    expect(
      canAccessPath("/future-section", {
        ...SELLER,
        isAdministrator: true,
        isSupportScoped: true,
      }),
    ).toBe(false);
  });

  it("fails closed for unknown admin routes and partial platform access", () => {
    const tenantViewer: RouteAccessContext = {
      ...SELLER,
      isAdministrator: true,
      hasTenant: false,
      permissions: [],
      platformCapabilities: ["platform.tenants.view"],
    };

    expect(canAccessPath("/admin", tenantViewer)).toBe(true);
    expect(canAccessPath("/admin/tenants", tenantViewer)).toBe(true);
    expect(canAccessPath("/admin/future", tenantViewer)).toBe(false);
    expect(canAccessPath("/audit", tenantViewer)).toBe(false);
  });

  it("limits platform access governance to an unscoped developer", () => {
    const developer: RouteAccessContext = {
      ...SELLER,
      isDeveloper: true,
      hasTenant: false,
      permissions: [],
      platformCapabilities: ["platform.access.view"],
    };

    expect(canAccessPath("/admin", developer)).toBe(true);
    expect(canAccessPath("/admin/access", developer)).toBe(true);
    expect(
      canAccessPath("/admin/access", {
        ...developer,
        isDeveloper: false,
        isAdministrator: true,
      }),
    ).toBe(false);
    expect(canAccessPath("/admin/access", { ...developer, isSupportScoped: true })).toBe(false);
  });
});
