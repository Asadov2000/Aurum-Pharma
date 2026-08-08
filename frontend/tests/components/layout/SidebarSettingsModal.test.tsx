import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { SidebarSettingsModal } from "@/components/layout/SidebarSettingsModal";
import {
  defaultSidebarPreferences,
  type SidebarPreferences,
} from "@/components/layout/sidebarPreferences";

const ITEMS = [
  { to: "/pos", label: "Касса" },
  { to: "/sales", label: "Чеки" },
  { to: "/catalog", label: "Каталог" },
] as const;

describe("SidebarSettingsModal", () => {
  it("shows only supplied routes and protects the open route", () => {
    render(
      <SidebarSettingsModal
        open
        items={ITEMS}
        preferences={defaultSidebarPreferences()}
        activeRoute="/pos"
        onChange={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByRole("dialog", { name: "Настроить меню" })).toBeInTheDocument();
    expect(screen.getByText("Касса")).toBeInTheDocument();
    expect(screen.getByText("Чеки")).toBeInTheDocument();
    expect(screen.queryByText("Роли")).not.toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Скрыть раздел «Касса»" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Авто" })).toHaveAttribute("aria-pressed", "true");
  });

  it("applies visibility, favorite, order and mode changes immediately", () => {
    let latest = defaultSidebarPreferences();

    function Harness(): JSX.Element {
      const [preferences, setPreferences] = useState(defaultSidebarPreferences());
      const update = (next: SidebarPreferences) => {
        latest = next;
        setPreferences(next);
      };
      return (
        <SidebarSettingsModal
          open
          items={ITEMS}
          preferences={preferences}
          activeRoute="/catalog"
          onChange={update}
          onClose={vi.fn()}
        />
      );
    }

    render(<Harness />);

    fireEvent.click(screen.getByRole("button", { name: "Компактный" }));
    expect(latest.desktopMode).toBe("compact");

    fireEvent.click(screen.getByRole("button", { name: "Добавить «Чеки» в избранное" }));
    expect(latest.favoriteRoutes).toEqual(["/sales"]);

    fireEvent.click(screen.getByRole("button", { name: "Поднять раздел «Чеки»" }));
    expect(latest.routeOrder.slice(0, 2)).toEqual(["/sales", "/pos"]);

    fireEvent.click(screen.getByRole("checkbox", { name: "Скрыть раздел «Чеки»" }));
    expect(latest.hiddenRoutes).toEqual(["/sales"]);
    expect(latest.favoriteRoutes).toEqual([]);

    fireEvent.click(screen.getByRole("button", { name: "Показать все" }));
    expect(latest.hiddenRoutes).toEqual([]);
  });

  it("filters rows without enabling order controls on a partial list", () => {
    render(
      <SidebarSettingsModal
        open
        items={ITEMS}
        preferences={defaultSidebarPreferences()}
        onChange={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    fireEvent.change(screen.getByLabelText("Найти раздел"), { target: { value: "чек" } });
    expect(screen.getByText("Чеки")).toBeInTheDocument();
    expect(screen.queryByText("Касса")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Поднять раздел «Чеки»" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Опустить раздел «Чеки»" })).toBeDisabled();
  });
});
