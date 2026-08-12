import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@tanstack/react-router", () => ({
  Link: ({
    children,
    to,
    className,
  }: {
    children: React.ReactNode;
    to: string;
    className?: string;
  }) => (
    <a href={to} className={className}>
      {children}
    </a>
  ),
}));

let mockUser: Record<string, unknown> = {};
vi.mock("@/features/auth/hooks", () => ({
  useAuth: () => ({ user: mockUser }),
}));

import { PlatformControlPage } from "@/features/platformControl/PlatformControlPage";

describe("PlatformControlPage", () => {
  beforeEach(() => {
    mockUser = {
      is_developer: false,
      is_administrator: true,
      platform_capabilities: ["platform.tenants.view"],
    };
  });

  it("shows only modules allowed by the active platform grant", () => {
    render(<PlatformControlPage />);

    expect(screen.getByRole("link", { name: /Аптеки/i })).toHaveAttribute("href", "/admin/tenants");
    expect(screen.queryByRole("link", { name: /Глобальный аудит/i })).not.toBeInTheDocument();
  });

  it("shows developer-only audit when the exact capability is present", () => {
    mockUser = {
      is_developer: true,
      is_administrator: false,
      platform_capabilities: ["platform.tenants.view", "platform.audit.global.view"],
    };
    render(<PlatformControlPage />);

    expect(screen.getByRole("link", { name: /Аптеки/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Глобальный аудит/i })).toHaveAttribute(
      "href",
      "/audit",
    );
  });

  it("shows platform access only to a developer with the exact capability", () => {
    mockUser = {
      is_developer: true,
      is_administrator: false,
      platform_capabilities: ["platform.access.view"],
    };
    const { rerender } = render(<PlatformControlPage />);

    expect(screen.getByRole("link", { name: /Доступ платформы/i })).toHaveAttribute(
      "href",
      "/admin/access",
    );

    mockUser = {
      is_developer: false,
      is_administrator: true,
      platform_capabilities: ["platform.access.view"],
    };
    rerender(<PlatformControlPage />);

    expect(screen.queryByRole("link", { name: /Доступ платформы/i })).not.toBeInTheDocument();
  });

  it("shows the Aurum team to an administrator with account visibility", () => {
    mockUser = {
      is_developer: false,
      is_administrator: true,
      platform_capabilities: ["platform.accounts.view"],
    };
    render(<PlatformControlPage />);

    expect(screen.getByRole("link", { name: /Команда Aurum/i })).toHaveAttribute(
      "href",
      "/admin/accounts",
    );
  });

  it("shows synchronization to an administrator with read capability", () => {
    mockUser = {
      is_developer: false,
      is_administrator: true,
      platform_capabilities: ["platform.sync.view"],
    };
    render(<PlatformControlPage />);

    expect(screen.getByRole("link", { name: /Синхронизация/i })).toHaveAttribute(
      "href",
      "/admin/sync",
    );
  });

  it("fails closed when the account has no platform capabilities", () => {
    mockUser = {
      is_developer: true,
      is_administrator: false,
      platform_capabilities: [],
    };
    render(<PlatformControlPage />);

    expect(screen.getByText(/не назначены инструменты управления платформой/i)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Аптеки" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Глобальный аудит" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Доступ платформы" })).not.toBeInTheDocument();
  });
});
