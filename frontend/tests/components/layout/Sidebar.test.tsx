import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { type ComponentPropsWithoutRef } from "react";

vi.mock("@tanstack/react-router", () => ({
  Link: ({ children, onClick, to, ...props }: ComponentPropsWithoutRef<"a"> & { to: string }) => (
    <a
      href={to}
      onClick={(event) => {
        event.preventDefault();
        onClick?.(event);
      }}
      {...props}
    >
      {children}
    </a>
  ),
  useRouterState: <T,>({ select }: { select: (state: { location: { pathname: string } }) => T }) =>
    select({ location: { pathname: "/pos" } }),
}));

import { Sidebar, type NavItem } from "@/components/layout/Sidebar";

const ITEMS: NavItem[] = [
  { to: "/", label: "Главная" },
  { to: "/pos", label: "Касса" },
  { to: "/roles", label: "Роли" },
];

describe("Sidebar", () => {
  it("renders only supplied routes and marks the current route", () => {
    const onOpenDrawer = vi.fn();
    render(<Sidebar items={ITEMS} onOpenDrawer={onOpenDrawer} />);

    expect(screen.getByRole("navigation", { name: "Основная навигация" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Главная" })).toHaveAttribute("href", "/");
    expect(screen.getByRole("link", { name: "Касса" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Роли" })).not.toHaveAttribute("aria-current");
    expect(screen.queryByRole("link", { name: "Биллинг" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Показать названия разделов" }));
    expect(onOpenDrawer).toHaveBeenCalledWith(expect.any(HTMLButtonElement));
  });

  it("shows section names and closes after drawer navigation", () => {
    const onNavigate = vi.fn();
    render(<Sidebar items={ITEMS} mode="drawer" onNavigate={onNavigate} />);

    expect(screen.getByText("Aurum Pharma")).toBeInTheDocument();
    expect(screen.getByText("Продажи")).toBeInTheDocument();
    expect(screen.getByText("Управление")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("link", { name: "Роли" }));
    expect(onNavigate).toHaveBeenCalledOnce();
  });
});
