import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { type MeResponse } from "@/features/auth/types";
import { type PersonalProfile } from "@/features/profile/types";
import { useAuthStore } from "@/stores/auth";

const state = vi.hoisted(() => ({
  data: undefined as PersonalProfile | undefined,
  isPending: false,
  isError: false,
  error: new Error("unavailable"),
  refetch: vi.fn(),
}));
vi.mock("@/features/profile/queries", () => ({ usePersonalProfileQuery: () => state }));
vi.mock("@/features/auth/AccountIdentityPanel", () => ({
  AccountIdentityPanel: function IdentityPanelFixture() {
    const [confirming, setConfirming] = useState(false);
    return (
      <section>
        Способы входа
        <button onClick={() => setConfirming(true)}>
          {confirming ? "Ожидается подтверждение" : "Начать подтверждение"}
        </button>
      </section>
    );
  },
}));
vi.mock("@tanstack/react-router", () => ({
  Link: ({ to, ...props }: { to: string; children: React.ReactNode }) => <a href={to} {...props} />,
}));

import { ProfilePage } from "@/features/profile/ProfilePage";

const profile: PersonalProfile = {
  id: "employee",
  full_name: "Сотрудник аптеки",
  email: "employee@example.test",
  status: "active",
  tenant_id: "tenant",
  is_tenant_owner: false,
  platform_role: null,
  access_assignments: [
    {
      role_id: "cashier",
      role_name: "Кассир",
      scope: "branch",
      branch: { id: "branch-1", name: "Аптека на Рудаки" },
    },
    { role_id: "inventory", role_name: "Учёт товаров", scope: "tenant", branch: null },
  ],
};

const previousAuth = useAuthStore.getState();
const identity: MeResponse = {
  id: profile.id,
  email: profile.email,
  full_name: profile.full_name,
  is_developer: false,
  is_administrator: false,
  home_tenant_id: profile.tenant_id,
  active_tenant_id: profile.tenant_id,
  status: "active",
  last_login_at: null,
  level: 1,
  is_tenant_owner: false,
  branch_assignments: {},
  permissions: [],
  platform_capabilities: [],
  support_access: null,
};

describe("personal profile", () => {
  beforeEach(() => {
    state.data = profile;
    state.isPending = false;
    state.isError = false;
    state.refetch.mockReset();
    useAuthStore.setState({ user: identity });
  });

  afterEach(() => {
    cleanup();
    useAuthStore.setState({ user: previousAuth.user });
  });

  it("shows actual role names and their scopes without editable identity or permissions", () => {
    render(<ProfilePage />);
    expect(screen.getByRole("heading", { name: profile.full_name, level: 2 })).toBeInTheDocument();
    const personalDetails = screen.getByText("ФИО").closest("dl");
    expect(personalDetails).not.toBeNull();
    expect(within(personalDetails!).getByText(profile.full_name)).toBeInTheDocument();
    expect(screen.getByText("Кассир")).toBeInTheDocument();
    expect(screen.getByText("Аптека на Рудаки")).toBeInTheDocument();
    expect(screen.getByText("Учёт товаров")).toBeInTheDocument();
    expect(screen.getByText("Все точки аптеки")).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.queryByText(/Уровень \d/)).not.toBeInTheDocument();
  });

  it("retains personal security and interface links without management links", () => {
    render(<ProfilePage />);
    expect(screen.getByRole("link", { name: "Безопасность аккаунта" })).toHaveAttribute(
      "href",
      "/security",
    );
    expect(screen.getByRole("link", { name: "Настройки интерфейса" })).toHaveAttribute(
      "href",
      "/settings",
    );
    expect(screen.getByRole("link", { name: "Уведомления" })).toHaveAttribute(
      "href",
      "/notifications",
    );
    expect(screen.getByRole("link", { name: "Открыть безопасность" })).toHaveAttribute(
      "href",
      "/security",
    );
    expect(screen.queryByRole("link", { name: "Сотрудники" })).not.toBeInTheDocument();
  });

  it.each(["", "   "])("shows identity fallbacks for missing name and email (%j)", (value) => {
    state.data = { ...profile, full_name: value, email: value };
    render(<ProfilePage />);
    expect(screen.getByRole("heading", { name: "Имя не указано", level: 2 })).toBeInTheDocument();
    const personalDetails = screen.getByText("ФИО").closest("dl");
    expect(personalDetails).not.toBeNull();
    expect(within(personalDetails!).getByText("Имя не указано")).toBeInTheDocument();
    expect(within(personalDetails!).getByText("Не указана")).toBeInTheDocument();
  });

  it("retains a complete long identity in the hero and personal details", () => {
    const fullName = "Сотрудник с длинным составным именем для рабочего профиля аптеки";
    const email = "long-profile-identity-with-a-complete-address@example.test";
    state.data = { ...profile, full_name: fullName, email };
    render(<ProfilePage />);
    expect(screen.getByRole("heading", { name: fullName, level: 2 })).toBeInTheDocument();
    const personalDetails = screen.getByText("ФИО").closest("dl");
    expect(personalDetails).not.toBeNull();
    expect(within(personalDetails!).getByText(fullName)).toBeInTheDocument();
    expect(within(personalDetails!).getByText(email)).toBeInTheDocument();
  });

  it.each<[string, Partial<MeResponse>, boolean]>([
    ["employee without management permissions", {}, true],
    ["pharmacy owner", { is_tenant_owner: true }, true],
    ["account without a tenant", { home_tenant_id: null, active_tenant_id: null }, false],
    ["developer with a tenant", { is_developer: true }, false],
    ["administrator with a tenant", { is_administrator: true }, false],
    [
      "scoped support account",
      {
        support_access: {
          id: "support-scope",
          tenant_id: "tenant",
          tenant_name: "Аптека",
          reason: "Profile fixture",
          capabilities: [],
          is_read_only: true,
          expires_at: "2030-01-01T00:00:00Z",
        },
      },
      false,
    ],
  ])("uses authenticated route access for the work link: %s", (_label, overrides, allowed) => {
    useAuthStore.setState({ user: { ...identity, ...overrides } });
    render(<ProfilePage />);
    const workLink = screen.queryByRole("link", { name: "Моя работа", exact: true });
    if (allowed) {
      expect(workLink).toHaveAttribute("href", "/my-work");
    } else {
      expect(workLink).not.toBeInTheDocument();
    }
  });

  it("does not infer work access from cached profile data when identity is unavailable", () => {
    useAuthStore.setState({ user: null });
    render(<ProfilePage />);
    expect(screen.queryByRole("link", { name: "Моя работа", exact: true })).not.toBeInTheDocument();
  });

  it("distinguishes platform identity from pharmacy assignments", () => {
    state.data = {
      ...profile,
      tenant_id: null,
      platform_role: "administrator",
      access_assignments: [],
    };
    render(<ProfilePage />);
    expect(screen.getByText("Администратор Aurum")).toBeInTheDocument();
    expect(screen.getByText("Личный аккаунт сотрудника Aurum")).toBeInTheDocument();
    expect(screen.getByText(/Назначений сотрудника аптеки/)).toBeInTheDocument();
    expect(screen.queryByText("Владелец аптеки")).not.toBeInTheDocument();
    expect(screen.queryByText("Кассир")).not.toBeInTheDocument();
  });

  it("shows loading without inventing role data", () => {
    state.data = undefined;
    state.isPending = true;
    render(<ProfilePage />);
    expect(screen.getByRole("status")).toHaveTextContent("Загрузка профиля");
    expect(screen.queryByText(profile.full_name)).not.toBeInTheDocument();
    expect(screen.queryByText(profile.email)).not.toBeInTheDocument();
    expect(screen.queryByText("Кассир")).not.toBeInTheDocument();
  });

  it("hides stale assignments after a refresh error and permits retry", () => {
    const { rerender } = render(<ProfilePage />);
    expect(screen.getByRole("heading", { name: profile.full_name, level: 2 })).toBeInTheDocument();
    state.isError = true;
    rerender(<ProfilePage />);
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.queryByText(profile.full_name)).not.toBeInTheDocument();
    expect(screen.queryByText(profile.email)).not.toBeInTheDocument();
    expect(screen.queryByText("Активен")).not.toBeInTheDocument();
    expect(screen.queryByText("Кассир")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Повторить" }));
    expect(state.refetch).toHaveBeenCalledOnce();
  });

  it("preserves an in-progress sign-in confirmation through profile refresh errors and recovery", () => {
    const { rerender } = render(<ProfilePage />);
    fireEvent.click(screen.getByRole("button", { name: "Начать подтверждение" }));

    state.isError = true;
    rerender(<ProfilePage />);
    expect(screen.getByRole("button", { name: "Ожидается подтверждение" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Повторить" }));
    expect(state.refetch).toHaveBeenCalledOnce();

    state.isError = false;
    rerender(<ProfilePage />);
    expect(screen.getByRole("heading", { name: profile.full_name, level: 2 })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Ожидается подтверждение" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Начать подтверждение" })).not.toBeInTheDocument();
  });
});
