import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const authMock = vi.hoisted(() => ({
  logout: vi.fn(),
  user: {
    id: "user-1",
    email: "owner@aurum.tj",
    full_name: "Owner",
    home_tenant_id: "tenant-1",
    is_administrator: false,
    is_developer: false,
    is_tenant_owner: false,
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
  Link: ({ children, to, ...props }: { children: React.ReactNode; to: string }) => (
    <a href={to} {...props}>
      {children}
    </a>
  ),
  useRouterState: <T,>({ select }: { select: (state: { location: { pathname: string } }) => T }) =>
    select({ location: { pathname: "/pos" } }),
  useNavigate: () => vi.fn(),
}));

vi.mock("@/components/layout/ServerStatusBanner", () => ({
  ServerStatusBanner: () => null,
}));

vi.mock("@/features/supportAccess/SupportAccessBanner", () => ({
  SupportAccessBanner: () => null,
}));

import { AppLayout } from "@/components/layout/AppLayout";

describe("AppLayout shell", () => {
  beforeEach(() => {
    window.localStorage.clear();
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
    window.localStorage.clear();
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
    expect(
      within(header as HTMLElement).getByRole("heading", { name: "Касса" }),
    ).toBeInTheDocument();
    expect(within(header as HTMLElement).getByTestId("runtime-surface-badge")).toBe(badge);
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
    const notices = banner.closest('[data-testid="app-shell-notices"]');
    const header = screen.getByTestId("app-shell-header");
    const main = screen.getByRole("main");

    expect(notices).not.toBeNull();
    expect(within(notices as HTMLElement).getByTestId("offline-status-banner")).toBe(banner);
    expect(within(header).getByText("Нет сети")).toBeInTheDocument();
    expect(within(main).queryByTestId("offline-status-banner")).not.toBeInTheDocument();
  });

  it("persists personalization without exposing routes outside permissions", async () => {
    render(
      <AppLayout>
        <section>Page content</section>
      </AppLayout>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Развернуть боковую панель" }));
    expect(
      JSON.parse(window.localStorage.getItem("aurum:sidebar:v1:user-1%3Atenant-1") ?? "{}"),
    ).toMatchObject({ desktopMode: "expanded" });

    fireEvent.click(screen.getByRole("button", { name: "Настроить боковую панель" }));
    const dialog = await screen.findByRole(
      "dialog",
      { name: "Настроить меню" },
      { timeout: 5_000 },
    );
    expect(within(dialog).getByText("Каталог")).toBeInTheDocument();
    expect(within(dialog).queryByText("Роли")).not.toBeInTheDocument();
    expect(within(dialog).getByRole("checkbox", { name: "Скрыть раздел «Касса»" })).toBeDisabled();

    fireEvent.click(within(dialog).getByRole("checkbox", { name: "Скрыть раздел «Каталог»" }));
    expect(screen.queryByRole("link", { name: "Каталог" })).not.toBeInTheDocument();
    expect(
      JSON.parse(window.localStorage.getItem("aurum:sidebar:v1:user-1%3Atenant-1") ?? "{}")
        .hiddenRoutes,
    ).toEqual(["/catalog"]);
  });
});

function setOnlineStatus(isOnline: boolean): void {
  Object.defineProperty(window.navigator, "onLine", {
    configurable: true,
    value: isOnline,
  });
}
