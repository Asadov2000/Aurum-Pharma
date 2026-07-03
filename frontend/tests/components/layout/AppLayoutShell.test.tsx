import { render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const authMock = vi.hoisted(() => ({
  logout: vi.fn(),
  user: {
    email: "owner@aurum.tj",
    full_name: "Owner",
    home_tenant_id: "tenant-1",
    is_administrator: false,
    is_developer: false,
    permissions: ["catalog.view", "pos.sell"],
  },
}));

vi.mock("@/features/auth/hooks", () => ({
  useAuth: () => ({
    logout: authMock.logout,
    user: authMock.user,
  }),
}));

vi.mock("@tanstack/react-router", () => ({
  Link: ({
    children,
    to,
    ...props
  }: {
    children: React.ReactNode;
    to: string;
  }) => (
    <a href={to} {...props}>
      {children}
    </a>
  ),
  useRouterState: <T,>({
    select,
  }: {
    select: (state: { location: { pathname: string } }) => T;
  }) => select({ location: { pathname: "/pos" } }),
}));

import { AppLayout } from "@/components/layout/AppLayout";

describe("AppLayout shell", () => {
  beforeEach(() => {
    setOnlineStatus(true);
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: vi.fn().mockImplementation((query: string) => ({
        addEventListener: vi.fn(),
        addListener: vi.fn(),
        dispatchEvent: vi.fn(),
        matches: false,
        media: query,
        onchange: null,
        removeEventListener: vi.fn(),
        removeListener: vi.fn(),
      })),
    });
  });

  afterEach(() => {
    setOnlineStatus(true);
  });

  it("renders the runtime badge in the header instead of page content", () => {
    render(
      <AppLayout>
        <section>Page content</section>
      </AppLayout>,
    );

    const badge = screen.getByTestId("runtime-surface-badge");
    const header = badge.closest("header");
    const main = screen.getByRole("main");

    expect(header).not.toBeNull();
    expect(within(header as HTMLElement).getByTestId("runtime-surface-badge")).toBe(
      badge,
    );
    expect(within(main).queryByTestId("runtime-surface-badge")).not.toBeInTheDocument();
    expect(within(main).getByText("Page content")).toBeInTheDocument();
  });

  it("renders the offline banner in the shell instead of page content", () => {
    setOnlineStatus(false);
    render(
      <AppLayout>
        <section>Page content</section>
      </AppLayout>,
    );

    const banner = screen.getByTestId("offline-status-banner");
    const stickyShell = banner.closest(".sticky");
    const main = screen.getByRole("main");

    expect(stickyShell).not.toBeNull();
    expect(within(stickyShell as HTMLElement).getByTestId("offline-status-banner")).toBe(
      banner,
    );
    expect(within(main).queryByTestId("offline-status-banner")).not.toBeInTheDocument();
  });
});

function setOnlineStatus(isOnline: boolean): void {
  Object.defineProperty(window.navigator, "onLine", {
    configurable: true,
    value: isOnline,
  });
}
