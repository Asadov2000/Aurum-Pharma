import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({
  pathname: "/catalog",
  user: null as Record<string, unknown> | null,
}));

vi.mock("@tanstack/react-router", () => ({
  Link: ({ children, to }: { children: React.ReactNode; to: string }) => (
    <a href={to}>{children}</a>
  ),
  useRouterState: <T,>({ select }: { select: (value: { location: { pathname: string } }) => T }) =>
    select({ location: { pathname: state.pathname } }),
}));

vi.mock("@/features/auth/hooks", () => ({
  useAuth: () => ({ user: state.user }),
}));

import { RouteAccessGuard } from "@/components/layout/RouteAccessGuard";

const SELLER = {
  id: "user-1",
  email: "seller@aurum.tj",
  full_name: "Seller",
  is_developer: false,
  is_administrator: false,
  home_tenant_id: "tenant-1",
  status: "active",
  last_login_at: null,
  level: 4,
  is_tenant_owner: false,
  branch_assignments: {},
  permissions: ["catalog.view", "pos.sell"],
};

describe("RouteAccessGuard", () => {
  beforeEach(() => {
    state.pathname = "/catalog";
    state.user = SELLER;
  });

  it("renders an allowed route", () => {
    render(
      <RouteAccessGuard>
        <div>Catalog content</div>
      </RouteAccessGuard>,
    );

    expect(screen.getByText("Catalog content")).toBeInTheDocument();
  });

  it("allows account-scoped settings without a tenant permission", () => {
    state.pathname = "/settings";

    render(
      <RouteAccessGuard>
        <div>Settings content</div>
      </RouteAccessGuard>,
    );

    expect(screen.getByText("Settings content")).toBeInTheDocument();
  });

  it("blocks an owner-only direct URL before the protected page mounts", () => {
    state.pathname = "/onboarding";

    render(
      <RouteAccessGuard>
        <div>Onboarding content</div>
      </RouteAccessGuard>,
    );

    expect(screen.queryByText("Onboarding content")).not.toBeInTheDocument();
    expect(screen.getByText("Раздел недоступен")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Перейти" })).toHaveAttribute("href", "/pos");
  });
});
