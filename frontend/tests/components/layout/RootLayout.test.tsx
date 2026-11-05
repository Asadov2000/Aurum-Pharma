import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({
  layoutMounts: 0,
  layoutUnmounts: 0,
  pathname: "/catalog",
  authenticated: true,
}));

vi.mock("@tanstack/react-router", () => ({
  Outlet: () => <div data-testid="route-outlet" />,
  useRouterState: ({ select }: { select: (value: { location: { pathname: string } }) => string }) =>
    select({ location: { pathname: state.pathname } }),
}));

vi.mock("@/features/auth/AuthGuard", () => ({
  AuthGuard: ({ children }: { children: React.ReactNode }) =>
    state.authenticated ? <>{children}</> : <div data-testid="auth-required" />,
}));

vi.mock("@/components/layout/AppLayout", async () => {
  const { useEffect } = await vi.importActual<typeof import("react")>("react");

  return {
    AppLayout: ({ children }: { children: React.ReactNode }) => {
      useEffect(() => {
        state.layoutMounts += 1;
        return () => {
          state.layoutUnmounts += 1;
        };
      }, []);

      return <div data-testid="app-layout">{children}</div>;
    },
  };
});

vi.mock("@/components/layout/RouteAccessGuard", () => ({
  RouteAccessGuard: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

import { RootLayout } from "@/components/layout/RootLayout";

describe("RootLayout", () => {
  it.each(["/login", "/activate-platform"])("does not mount protected shell on %s", (pathname) => {
    state.pathname = pathname;
    state.authenticated = false;
    state.layoutMounts = 0;
    render(<RootLayout />);
    expect(screen.getByTestId("route-outlet")).toBeInTheDocument();
    expect(screen.queryByTestId("app-layout")).not.toBeInTheDocument();
    expect(state.layoutMounts).toBe(0);
  });

  it("keeps the shell behind the authentication guard", () => {
    state.pathname = "/catalog";
    state.authenticated = false;
    state.layoutMounts = 0;
    render(<RootLayout />);
    expect(screen.getByTestId("auth-required")).toBeInTheDocument();
    expect(screen.queryByTestId("app-layout")).not.toBeInTheDocument();
    expect(state.layoutMounts).toBe(0);
  });

  it("keeps the application shell mounted between protected sections", async () => {
    state.authenticated = true;
    state.pathname = "/catalog";
    state.layoutMounts = 0;
    state.layoutUnmounts = 0;
    const view = render(<RootLayout />);

    expect(await screen.findByTestId("app-layout")).toBeInTheDocument();
    expect(state.layoutMounts).toBe(1);

    state.pathname = "/sales";
    view.rerender(<RootLayout />);

    expect(screen.getByTestId("app-layout")).toBeInTheDocument();
    expect(state.layoutMounts).toBe(1);
    expect(state.layoutUnmounts).toBe(0);

    state.pathname = "/login";
    view.rerender(<RootLayout />);

    expect(screen.queryByTestId("app-layout")).not.toBeInTheDocument();
    expect(screen.getByTestId("route-outlet")).toBeInTheDocument();
    expect(state.layoutUnmounts).toBe(1);

    state.pathname = "/activate-platform";
    view.rerender(<RootLayout />);

    expect(screen.queryByTestId("app-layout")).not.toBeInTheDocument();
    expect(screen.getByTestId("route-outlet")).toBeInTheDocument();
  });
});
