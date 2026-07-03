import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

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

  it("renders the runtime badge in the header instead of page content", () => {
    render(
      <AppLayout>
        <section>Рабочая область</section>
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
    expect(within(main).getByText("Рабочая область")).toBeInTheDocument();
  });
});
