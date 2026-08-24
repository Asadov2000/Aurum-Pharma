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
  it("renders only supplied routes, marks the current route and toggles its width", () => {
    const onToggleExpanded = vi.fn();
    const onOpenSettings = vi.fn();
    render(
      <Sidebar items={ITEMS} onToggleExpanded={onToggleExpanded} onOpenSettings={onOpenSettings} />,
    );

    const navigation = screen.getByRole("navigation", { name: "Основная навигация" });
    expect(navigation).toHaveAttribute("data-sidebar-mode", "expanded");
    expect(navigation.querySelector(".aurum-scrollbar")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Главная" })).toHaveAttribute("href", "/");
    expect(screen.getByRole("link", { name: "Касса" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Роли" })).not.toHaveAttribute("aria-current");
    expect(screen.queryByRole("link", { name: "Тариф и оплата" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Настроить боковую панель" }));
    expect(onOpenSettings).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole("button", { name: "Свернуть боковую панель" }));
    expect(onToggleExpanded).toHaveBeenCalledOnce();
  });

  it("puts favorites first without duplicating links", () => {
    render(<Sidebar items={ITEMS} favoriteRoutes={["/roles"]} />);

    expect(screen.getByText("Избранное")).toBeInTheDocument();
    expect(screen.getAllByRole("link").map((link) => link.getAttribute("href"))).toEqual([
      "/roles",
      "/",
      "/pos",
    ]);
    expect(screen.getAllByRole("link", { name: "Роли" })).toHaveLength(1);
  });

  it("keeps accessible names and native hints in compact mode", () => {
    render(<Sidebar items={ITEMS} expanded={false} />);

    expect(screen.getByRole("navigation", { name: "Основная навигация" })).toHaveAttribute(
      "data-sidebar-mode",
      "compact",
    );
    expect(screen.getByRole("link", { name: "Касса" })).toHaveAttribute("title", "Касса");
    expect(screen.queryByText("Продажи")).not.toBeInTheDocument();
  });

  it("shows section names and closes after drawer navigation", () => {
    const onNavigate = vi.fn();
    const onOpenSettings = vi.fn();
    render(
      <Sidebar
        items={ITEMS}
        mode="drawer"
        onNavigate={onNavigate}
        onOpenSettings={onOpenSettings}
      />,
    );

    expect(screen.getByText("Aurum Pharma")).toBeInTheDocument();
    expect(screen.getByText("Продажи")).toBeInTheDocument();
    expect(screen.getByText("Управление")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("link", { name: "Роли" }));
    expect(onNavigate).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole("button", { name: "Настроить боковую панель" }));
    expect(onOpenSettings).toHaveBeenCalledOnce();
  });
});
