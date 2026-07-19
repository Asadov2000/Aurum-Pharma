import { describe, expect, it } from "vitest";

import {
  canAccessPath,
  firstAccessiblePath,
  type RouteAccessContext,
} from "@/components/layout/routeAccess";

const SELLER: RouteAccessContext = {
  isDeveloper: false,
  isAdministrator: false,
  isTenantOwner: false,
  hasTenant: true,
  permissions: ["catalog.view", "pos.shift_open", "pos.sell", "sales.view.own", "audit.view.own"],
};

describe("route access", () => {
  it("allows only explicitly granted seller sections", () => {
    expect(canAccessPath("/pos", SELLER)).toBe(true);
    expect(canAccessPath("/catalog", SELLER)).toBe(true);
    expect(canAccessPath("/sales", SELLER)).toBe(true);
    expect(canAccessPath("/audit", SELLER)).toBe(true);
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
      isTenantOwner: false,
      hasTenant: false,
      permissions: [],
    };

    expect(canAccessPath("/", support)).toBe(true);
    expect(canAccessPath("/admin/tenants", support)).toBe(true);
    expect(canAccessPath("/notifications", support)).toBe(true);
    expect(canAccessPath("/catalog", support)).toBe(false);
    expect(canAccessPath("/roles", support)).toBe(false);
  });

  it("allows global audit only to an unscoped developer", () => {
    const developer: RouteAccessContext = {
      isDeveloper: true,
      isAdministrator: false,
      isTenantOwner: false,
      hasTenant: false,
      permissions: [],
    };
    const administrator: RouteAccessContext = {
      ...developer,
      isDeveloper: false,
      isAdministrator: true,
    };

    expect(canAccessPath("/audit", developer)).toBe(true);
    expect(canAccessPath("/audit", administrator)).toBe(false);
  });

  it("allows the filtered role catalogue in an explicit support tenant context", () => {
    const scopedSupport: RouteAccessContext = {
      isDeveloper: false,
      isAdministrator: true,
      isTenantOwner: false,
      hasTenant: true,
      permissions: [],
    };

    expect(canAccessPath("/roles", scopedSupport)).toBe(true);
    expect(canAccessPath("/", scopedSupport)).toBe(false);
    expect(canAccessPath("/catalog", scopedSupport)).toBe(false);
  });

  it("leaves unknown paths to the router", () => {
    expect(canAccessPath("/future-section", SELLER)).toBe(true);
  });
});
