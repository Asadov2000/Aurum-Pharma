import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { type PersonalProfile } from "@/features/profile/types";

const state = vi.hoisted(() => ({
  data: undefined as PersonalProfile | undefined,
  isPending: false,
  isError: false,
  error: new Error("unavailable"),
  refetch: vi.fn(),
}));
vi.mock("@/features/profile/queries", () => ({ usePersonalProfileQuery: () => state }));
vi.mock("@/features/auth/AccountIdentityPanel", () => ({
  AccountIdentityPanel: () => <section>Способы входа</section>,
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

describe("personal profile", () => {
  beforeEach(() => {
    state.data = profile;
    state.isPending = false;
    state.isError = false;
    state.refetch.mockReset();
  });

  it("shows actual role names and their scopes without editable identity or permissions", () => {
    render(<ProfilePage />);
    expect(screen.getByText(profile.full_name)).toBeInTheDocument();
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
    expect(screen.queryByRole("link", { name: "Сотрудники" })).not.toBeInTheDocument();
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
    expect(screen.getByText(/Назначений сотрудника аптеки/)).toBeInTheDocument();
    expect(screen.queryByText("Владелец аптеки")).not.toBeInTheDocument();
    expect(screen.queryByText("Кассир")).not.toBeInTheDocument();
  });

  it("shows loading without inventing role data", () => {
    state.data = undefined;
    state.isPending = true;
    render(<ProfilePage />);
    expect(screen.getByRole("status")).toHaveTextContent("Загрузка профиля");
    expect(screen.queryByText("Кассир")).not.toBeInTheDocument();
  });

  it("hides stale assignments after a refresh error and permits retry", () => {
    state.isError = true;
    render(<ProfilePage />);
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.queryByText("Кассир")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Повторить" }));
    expect(state.refetch).toHaveBeenCalledOnce();
  });
});
