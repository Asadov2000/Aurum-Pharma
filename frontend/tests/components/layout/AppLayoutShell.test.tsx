import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
const layoutState = vi.hoisted(() => ({
  navigate: vi.fn(),
  pathname: "/pos",
  preferences: undefined as
    | {
        workspace: {
          desktop_mode: "auto" | "compact" | "expanded";
          hidden_routes: string[];
          favorite_routes: string[];
          route_order: string[];
          start_route: string;
        };
      }
    | undefined,
}));

vi.mock("@/features/auth/hooks", () => ({
  useAuth: () => ({
    logout: authMock.logout,
    user: authMock.user,
  }),
}));

vi.mock("@/features/auth/AccountSecuritySurface", () => ({ default: () => null }));

vi.mock("@tanstack/react-router", () => ({
  Link: ({ children, to, ...props }: { children: React.ReactNode; to: string }) => (
    <a href={to} {...props}>
      {children}
    </a>
  ),
  useRouterState: <T,>({ select }: { select: (state: { location: { pathname: string } }) => T }) =>
    select({ location: { pathname: layoutState.pathname } }),
  useNavigate: () => layoutState.navigate,
}));

vi.mock("@/components/AppearanceMenu", () => ({
  AppearanceMenu: () => <span data-testid="appearance-menu-ready" />,
}));

vi.mock("@/features/supportAccess/SupportAccessBanner", () => ({
  SupportAccessBanner: () => null,
}));

vi.mock("@/features/settings/queries", () => ({
  useUserPreferencesQuery: () => ({ data: layoutState.preferences }),
  useUpdateUserPreferences: () => ({ isPending: false, mutate: vi.fn() }),
}));

import { AppLayout } from "@/components/layout/AppLayout";
import { Modal } from "@/components/ui/Modal";

describe("AppLayout shell", () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.sessionStorage.clear();
    layoutState.navigate.mockReset();
    layoutState.pathname = "/pos";
    layoutState.preferences = undefined;
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

  it("renders the runtime badge in the header instead of page content", async () => {
    render(
      <AppLayout>
        <section>Page content</section>
      </AppLayout>,
    );
    await waitFor(() => expect(screen.getByTestId("appearance-menu-ready")).toBeInTheDocument());

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

  it("keeps the page locked when the mobile drawer closes before another modal", async () => {
    const originalOverflow = document.body.style.overflow;
    const content = (modalOpen: boolean) => (
      <AppLayout>
        <Modal open={modalOpen} onClose={() => undefined} title="Другое окно">
          Modal content
        </Modal>
      </AppLayout>
    );
    const view = render(content(false));
    fireEvent.click(screen.getByRole("button", { name: "Открыть меню", exact: true }));
    await within(screen.getByRole("dialog", { name: "Меню приложения" })).findByRole("navigation", {
      name: "Основная навигация",
    });
    view.rerender(content(true));
    fireEvent.click(screen.getByRole("button", { name: "Закрыть меню", exact: true }));
    expect(screen.queryByRole("dialog", { name: "Меню приложения" })).not.toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "Другое окно" })).toBeInTheDocument();
    expect(document.body.style.overflow).toBe("hidden");

    view.rerender(content(false));
    expect(document.body.style.overflow).toBe(originalOverflow);
  });

  it("renders the offline banner in the shell instead of page content", () => {
    setOnlineStatus(false);
    render(
      <AppLayout>
        <section>Page content</section>
      </AppLayout>,
    );

    const banner = screen.getByText(/^Нет интернета/);
    const notices = banner.parentElement;
    const header = screen.getByTestId("app-shell-header");
    const main = screen.getByRole("main");

    expect(notices).not.toBeNull();
    expect(within(notices as HTMLElement).getByText(/^Нет интернета/)).toBe(banner);
    expect(within(header).getByText("Нет сети")).toBeInTheDocument();
    expect(within(main).queryByText(/^Нет интернета/)).not.toBeInTheDocument();
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

  it("applies account menu preferences without opening the settings page", async () => {
    layoutState.preferences = {
      workspace: {
        desktop_mode: "expanded",
        hidden_routes: ["/catalog"],
        favorite_routes: ["/pos"],
        route_order: ["/pos", "/catalog"],
        start_route: "/pos",
      },
    };

    render(
      <AppLayout>
        <section>Page content</section>
      </AppLayout>,
    );

    await waitFor(() => {
      expect(screen.queryByRole("link", { name: "Каталог" })).not.toBeInTheDocument();
      expect(
        JSON.parse(window.localStorage.getItem("aurum:sidebar:v1:user-1%3Atenant-1") ?? "{}"),
      ).toMatchObject({ desktopMode: "expanded", hiddenRoutes: ["/catalog"] });
    });
  });

  it("opens the saved accessible start route only once per browser session", async () => {
    layoutState.pathname = "/";
    layoutState.preferences = {
      workspace: {
        desktop_mode: "auto",
        hidden_routes: [],
        favorite_routes: [],
        route_order: [],
        start_route: "/pos",
      },
    };

    const view = render(
      <AppLayout>
        <section>Page content</section>
      </AppLayout>,
    );

    await waitFor(() =>
      expect(layoutState.navigate).toHaveBeenCalledWith({ to: "/pos", replace: true }),
    );
    layoutState.navigate.mockClear();
    view.unmount();
    render(
      <AppLayout>
        <section>Page content</section>
      </AppLayout>,
    );
    await new Promise((resolve) => window.setTimeout(resolve, 0));
    expect(layoutState.navigate).not.toHaveBeenCalled();
  });
});

function setOnlineStatus(isOnline: boolean): void {
  Object.defineProperty(window.navigator, "onLine", {
    configurable: true,
    value: isOnline,
  });
}
