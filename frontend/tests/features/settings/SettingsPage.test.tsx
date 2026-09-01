import { fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({
  isOwner: false,
  isDeveloper: false,
  isAdministrator: false,
  isSupportScoped: false,
}));

vi.mock("@/features/auth/hooks", () => ({
  useAuth: () => ({
    user: {
      id: "user-1",
      active_tenant_id: "tenant-1",
      is_tenant_owner: state.isOwner,
      is_developer: state.isDeveloper,
      is_administrator: state.isAdministrator,
      support_access: state.isSupportScoped ? { id: "support-session" } : null,
    },
  }),
}));

vi.mock("@/features/settings/queries", () => ({
  settingsKeys: { preferencesUpdate: ["settings", "preferences", "update"] },
  useUserPreferencesQuery: () => ({
    data: undefined,
    error: null,
    isFetching: false,
  }),
}));

vi.mock("@/features/settings/AccountAndMenuPanels", () => ({
  AccountSettingsPanel: () => <div>Панель аккаунта</div>,
  MenuSettingsPanel: () => <div>Панель меню</div>,
}));

vi.mock("@/features/settings/DeviceSettingsPanel", () => ({
  DeviceSettingsPanel: () => <div>Панель устройства</div>,
}));

vi.mock("@/features/settings/InterfaceSettingsPanel", () => ({
  InterfaceSettingsPanel: () => <div>Панель интерфейса</div>,
}));

vi.mock("@/features/settings/OwnerSettingsPanel", () => ({
  OwnerSettingsPanel: ({ section }: { section: string }) => <div>Панель владельца: {section}</div>,
}));

vi.mock("@/features/auth/SecurityPage", () => ({
  SecurityPage: () => <div>Панель безопасности</div>,
}));

vi.mock("@/features/notifications/SubscriptionsForm", () => ({
  SubscriptionsForm: () => <div>Панель уведомлений</div>,
}));

import { SettingsPage } from "@/features/settings/SettingsPage";

function renderPage(): void {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <SettingsPage />
    </QueryClientProvider>,
  );
}

describe("SettingsPage access boundaries", () => {
  beforeEach(() => {
    window.history.replaceState({}, "", "/settings");
    state.isOwner = false;
    state.isDeveloper = false;
    state.isAdministrator = false;
    state.isSupportScoped = false;
  });

  it("shows personal and device settings without exposing owner categories", () => {
    renderPage();

    expect(screen.getByRole("button", { name: /Мой аккаунт/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Касса и оборудование/ })).toBeInTheDocument();
    expect(screen.getByText("Только для вас")).toBeInTheDocument();
    expect(screen.getByText("На этом устройстве")).toBeInTheDocument();
    expect(screen.queryByText("Для всей аптеки")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Рабочие правила/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Оплата и возвраты/ })).not.toBeInTheDocument();
  });

  it("shows owner categories only to the active tenant owner", () => {
    state.isOwner = true;
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: /Оплата и возвраты/ }));

    expect(screen.getByText("Для всей аптеки")).toBeInTheDocument();
    expect(screen.getByText("Панель владельца: sales")).toBeInTheDocument();
  });

  it("opens the owner section selected by a Start deep link", () => {
    state.isOwner = true;
    window.history.replaceState({}, "", "/settings?section=sales");
    renderPage();

    expect(screen.getByText("Панель владельца: sales")).toBeInTheDocument();
  });

  it("hides owner categories from platform identities even if ownership is present", () => {
    state.isOwner = true;
    state.isDeveloper = true;
    renderPage();

    expect(screen.queryByText("Для всей аптеки")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Рабочие правила/ })).not.toBeInTheDocument();
  });

  it("filters the navigation without changing the active panel", () => {
    renderPage();

    fireEvent.change(screen.getByRole("textbox", { name: "Найти настройку" }), {
      target: { value: "сканер" },
    });

    expect(screen.getByRole("button", { name: /Касса и оборудование/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Мой аккаунт/ })).not.toBeInTheDocument();
    expect(screen.getByText("Панель интерфейса")).toBeInTheDocument();
  });
});
