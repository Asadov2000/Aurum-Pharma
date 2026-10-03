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
    isAuthenticated: true,
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

vi.mock("@/features/tenantAccess/TenantAccessBanner", () => ({
  TenantAccessBanner: () => <span data-testid="tenant-access-banner" />,
}));

vi.mock("@/features/settings/queries", () => ({
  useUserPreferencesQuery: () => ({ data: layoutState.preferences }),
  useUpdateUserPreferences: () => ({ isPending: false, mutate: vi.fn() }),
}));

import { AppLayout } from "@/components/layout/AppLayout";
import { ContextHelpPortal } from "@/components/layout/ContextHelpPortal";
import { Modal } from "@/components/ui/Modal";
import { ConnectivityContext } from "@/lib/connectivityContext";

describe("AppLayout shell", () => {
  it.each(["payments", "refunds"] as const)(
    "explains %s reconciliation offline without executing an operation",
    async (operationKind) => {
      layoutState.pathname = "/payment-reconciliation";
      setOnlineStatus(false);
      const refresh = vi.fn();
      const resolve = vi.fn();
      render(
        <AppLayout>
          <ContextHelpPortal pathname="/payment-reconciliation" operationKind={operationKind} />
          <button onClick={refresh}>Обновить</button>
          <button onClick={resolve}>Принять решение</button>
        </AppLayout>,
      );
      fireEvent.click(await screen.findByRole("button", { name: "Помощь по разделу" }));
      const help = screen.getByRole("dialog", { name: "Помощь по разделу" });
      expect(
        within(help).getByText(
          operationKind === "refunds"
            ? "Проверьте результат возврата"
            : "Проверьте результат оплаты",
        ),
      ).toBeInTheDocument();
      fireEvent.click(within(help).getByRole("button", { name: "Инструкция по шагам" }));
      expect(
        within(help).getByText(
          operationKind === "refunds" ? /Статус «Ждут чека»/ : /ещё может ждать завершения чека/,
        ),
      ).toBeInTheDocument();
      fireEvent.click(within(help).getByRole("button", { name: "Показать на экране" }));
      expect(screen.getByRole("button", { name: "Обновить" })).toHaveClass("aurum-help-target");
      expect(refresh).not.toHaveBeenCalled();
      expect(resolve).not.toHaveBeenCalled();
    },
  );

  it("resets reconciliation help when the operation kind changes", async () => {
    layoutState.pathname = "/payment-reconciliation";
    const view = (operationKind: "payments" | "refunds") => (
      <AppLayout>
        <ContextHelpPortal pathname="/payment-reconciliation" operationKind={operationKind} />
        <button disabled>Обновить</button>
      </AppLayout>
    );
    const { rerender } = render(view("payments"));
    fireEvent.click(await screen.findByRole("button", { name: "Помощь по разделу" }));
    fireEvent.click(screen.getByRole("button", { name: "Инструкция по шагам" }));
    rerender(view("refunds"));
    expect(screen.queryByRole("dialog", { name: "Помощь по разделу" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Помощь по разделу" }));
    const help = screen.getByRole("dialog", { name: "Помощь по разделу" });
    expect(within(help).getByText("Проверьте результат возврата")).toBeInTheDocument();
    expect(within(help).queryByRole("button", { name: "Показать на экране" })).toBeNull();
    expect(within(help).queryByRole("list")).toBeNull();
  });

  beforeEach(() => {
    window.localStorage.clear();
    window.sessionStorage.clear();
    layoutState.navigate.mockReset();
    layoutState.pathname = "/pos";
    layoutState.preferences = undefined;
    authMock.user.permissions = ["catalog.view", "pos.sell"];
    authMock.logout.mockReset();
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

  it("highlights the cashier action without completing a sale", async () => {
    const complete = vi.fn();
    render(
      <AppLayout>
        <ContextHelpPortal pathname="/pos" />
        <button type="button" onClick={complete}>
          Завершить продажу
        </button>
      </AppLayout>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("appearance-menu-ready")).toBeInTheDocument();
      expect(screen.getByTestId("tenant-access-banner")).toBeInTheDocument();
    });
    fireEvent.click(await screen.findByRole("button", { name: "Помощь по разделу" }));
    const help = screen.getByRole("dialog", { name: "Помощь по разделу" });
    expect(
      await within(help).findByText("Проверьте оплату и завершите продажу"),
    ).toBeInTheDocument();
    fireEvent.click(within(help).getByRole("button", { name: "Показать на экране" }));

    const action = screen.getByRole("button", { name: "Завершить продажу" });
    expect(complete).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Помощь по разделу" })).toHaveFocus();
    expect(action).toHaveClass("aurum-help-target");
    fireEvent.pointerDown(action);
    expect(action).not.toHaveClass("aurum-help-target");
  });

  it("does not spotlight a disabled incoming acceptance action", async () => {
    layoutState.pathname = "/incoming/document-1";
    render(
      <AppLayout>
        <ContextHelpPortal pathname="/incoming/detail" />
        <button type="button" disabled>
          Принять на склад
        </button>
      </AppLayout>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("appearance-menu-ready")).toBeInTheDocument();
      expect(screen.getByTestId("tenant-access-banner")).toBeInTheDocument();
    });
    fireEvent.click(await screen.findByRole("button", { name: "Помощь по разделу" }));
    const help = screen.getByRole("dialog", { name: "Помощь по разделу" });
    await within(help).findByText("Просмотрите документ");
    expect(within(help).queryByRole("button", { name: "Показать на экране" })).toBeNull();
    expect(within(help).getByText(/Принятый документ нельзя изменить/)).toBeInTheDocument();
  });

  it("ignores hidden actions and clears the spotlight when the route changes", async () => {
    layoutState.pathname = "/incoming/document-1";
    const view = render(
      <AppLayout>
        <ContextHelpPortal pathname="/incoming/detail" />
        <div style={{ display: "none" }}>
          <button type="button">Продолжить</button>
        </div>
        <button type="button">Добавить позицию</button>
      </AppLayout>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("appearance-menu-ready")).toBeInTheDocument();
      expect(screen.getByTestId("tenant-access-banner")).toBeInTheDocument();
    });
    fireEvent.click(await screen.findByRole("button", { name: "Помощь по разделу" }));
    const help = screen.getByRole("dialog", { name: "Помощь по разделу" });
    expect(await within(help).findByText("Проверьте позиции поставки")).toBeInTheDocument();
    fireEvent.click(within(help).getByRole("button", { name: "Показать на экране" }));
    const action = screen.getByRole("button", { name: "Добавить позицию" });
    expect(action).toHaveClass("aurum-help-target");

    layoutState.pathname = "/staff-pay";
    view.rerender(<AppLayout>Другой раздел</AppLayout>);
    await waitFor(() => expect(action).not.toHaveClass("aurum-help-target"));
  });

  it("keeps cash payout guidance independent from actual employee records", async () => {
    layoutState.pathname = "/staff-pay";
    render(
      <AppLayout>
        <ContextHelpPortal pathname="/staff-pay" />
        <label htmlFor="pay-employee">Найти сотрудника</label>
        <input id="pay-employee" />
      </AppLayout>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("appearance-menu-ready")).toBeInTheDocument();
      expect(screen.getByTestId("tenant-access-banner")).toBeInTheDocument();
    });
    fireEvent.click(await screen.findByRole("button", { name: "Помощь по разделу" }));
    const help = screen.getByRole("dialog", { name: "Помощь по разделу" });
    await within(help).findByText("Найдите сотрудника");
    fireEvent.click(within(help).getByRole("button", { name: "Инструкция по шагам" }));
    expect(within(help).getByText(/После фактической выдачи наличных/)).toBeInTheDocument();
    expect(within(help).getByText(/Запись выплаты не выдаёт деньги/)).toBeInTheDocument();
    fireEvent.click(within(help).getByRole("button", { name: "Показать на экране" }));
    expect(screen.getByRole("textbox", { name: "Найти сотрудника" })).toHaveFocus();
  });

  it.each([
    ["/pos", "Касса"],
    ["/billing", "Расчёты с Aurum"],
  ])("keeps the header and runtime badge for %s", async (pathname, title) => {
    layoutState.pathname = pathname;
    render(
      <AppLayout>
        <section>Page content</section>
      </AppLayout>,
    );
    await waitFor(() => expect(screen.getByTestId("appearance-menu-ready")).toBeInTheDocument());

    expect(await screen.findByTestId("tenant-access-banner")).toBeInTheDocument();
    const header = screen.getByTestId("app-shell-header");
    fireEvent.click(within(header).getByRole("button", { name: "Меню профиля" }));
    const profileMenu = screen.getByRole("dialog", { name: "Меню профиля" });
    const badge = await within(profileMenu).findByTestId("runtime-surface-badge");
    const main = screen.getByRole("main");

    expect(header).not.toBeNull();
    expect(within(header as HTMLElement).getByRole("heading", { name: title })).toBeInTheDocument();
    expect(within(profileMenu).getByTestId("runtime-surface-badge")).toBe(badge);
    expect(within(main).queryByTestId("runtime-surface-badge")).not.toBeInTheDocument();
    expect(within(main).getByText("Page content")).toBeInTheDocument();
    expect(within(main).queryByTestId("tenant-access-banner")).not.toBeInTheDocument();
  });

  it("opens the personal profile from the header without user-management permissions", async () => {
    authMock.user.permissions = [];
    layoutState.pathname = "/profile";
    render(
      <AppLayout>
        <section>Personal profile content</section>
      </AppLayout>,
    );

    const header = screen.getByTestId("app-shell-header");
    fireEvent.click(within(header).getByRole("button", { name: "Меню профиля" }));
    const menu = screen.getByRole("dialog", { name: "Меню профиля" });
    const profile = await within(menu).findByRole("link", {
      name: "Мой профиль",
      exact: true,
    });
    expect(profile).toHaveAttribute("href", "/profile");
    expect(within(menu).getByText(authMock.user.full_name)).toBeInTheDocument();
    expect(within(header).queryByText(authMock.user.email)).not.toBeInTheDocument();
    expect(within(screen.getByRole("main")).getByText("Personal profile content")).toBeVisible();
  });

  it("filters quick links and search results when permissions are withdrawn", async () => {
    const view = render(<AppLayout>Content</AppLayout>);
    const shortcuts = screen.getByRole("navigation", { name: "Быстрые переходы" });
    expect(within(shortcuts).getByRole("link", { name: "Каталог" })).toBeInTheDocument();
    expect(within(shortcuts).queryByRole("link", { name: "Отчёты" })).not.toBeInTheDocument();
    fireEvent.keyDown(document, { key: "k", ctrlKey: true });
    const finder = await screen.findByRole("dialog", { name: "Перейти в раздел" });
    expect(within(finder).getByRole("button", { name: "Каталог" })).toBeInTheDocument();

    authMock.user.permissions = ["pos.sell"];
    view.rerender(<AppLayout>Content</AppLayout>);
    expect(within(shortcuts).queryByRole("link", { name: "Каталог" })).not.toBeInTheDocument();
    expect(within(finder).queryByRole("button", { name: "Каталог" })).not.toBeInTheDocument();
    fireEvent.click(within(finder).getByRole("button", { name: "Касса" }));
    expect(layoutState.navigate).toHaveBeenCalledWith({ to: "/pos" });
    expect(screen.queryByRole("dialog", { name: "Перейти в раздел" })).not.toBeInTheDocument();
  });

  it("uses saved favorites without restoring hidden or unavailable routes", () => {
    window.localStorage.setItem(
      "aurum:sidebar:v1:user-1%3Atenant-1",
      JSON.stringify({
        hiddenRoutes: ["/catalog"],
        favoriteRoutes: ["/pos", "/catalog", "/reports"],
      }),
    );
    render(<AppLayout>Content</AppLayout>);
    const shortcuts = screen.getByRole("navigation", { name: "Быстрые переходы" });
    expect(
      within(shortcuts)
        .getAllByRole("link")
        .map((link) => link.getAttribute("href")),
    ).toEqual(["/pos"]);
  });

  it("checks real connection state and restores focus after closing the profile menu", () => {
    const checkNow = vi.fn();
    render(
      <ConnectivityContext.Provider
        value={{ status: "server-unavailable", canUseServer: false, checkNow }}
      >
        <AppLayout>Content</AppLayout>
      </ConnectivityContext.Provider>,
    );
    const connection = screen.getByRole("button", { name: "Состояние соединения" });
    fireEvent.click(connection);
    const details = screen.getByRole("dialog", { name: "Состояние соединения" });
    expect(within(details).getByText(/Сервис недоступен/)).toBeInTheDocument();
    fireEvent.click(within(details).getByRole("button", { name: "Проверить соединение" }));
    expect(checkNow).toHaveBeenCalledOnce();

    const profile = screen.getByRole("button", { name: "Меню профиля" });
    fireEvent.click(profile);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "Меню профиля" })).not.toBeInTheDocument();
    expect(profile).toHaveFocus();
    fireEvent.click(profile);
    fireEvent.click(screen.getByRole("button", { name: "Выйти", exact: true }));
    expect(authMock.logout).toHaveBeenCalledOnce();
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
    authMock.user.permissions.push("billing.overview.view", "billing.invoice.view");
    layoutState.pathname = "/";
    layoutState.preferences = {
      workspace: {
        desktop_mode: "expanded",
        hidden_routes: ["/catalog"],
        favorite_routes: ["/billing", "/pos"],
        route_order: ["/billing", "/pos", "/catalog"],
        start_route: "/billing",
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
    expect(screen.queryByRole("link", { name: "Расчёты с Aurum" })).not.toBeInTheDocument();
    expect(layoutState.navigate).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Настроить боковую панель" }));
    const dialog = await screen.findByRole("dialog", { name: "Настроить меню" });
    expect(within(dialog).queryByText("Расчёты с Aurum")).not.toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "Показать все" }));
    expect(screen.queryByRole("link", { name: "Расчёты с Aurum" })).not.toBeInTheDocument();
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
