import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  ConfigurableFilterBar,
  type ConfigurableFilter,
} from "@/components/ui/ConfigurableFilterBar";

function filters(onClear = vi.fn()): ConfigurableFilter[] {
  return [
    {
      id: "search",
      label: "Поиск",
      content: (
        <label>
          Поиск
          <input />
        </label>
      ),
      active: false,
      onClear: vi.fn(),
      alwaysVisible: true,
    },
    {
      id: "status",
      label: "Статус",
      content: (
        <label>
          Статус
          <select />
        </label>
      ),
      active: true,
      onClear,
      defaultVisible: true,
    },
    {
      id: "branch",
      label: "Точка",
      content: (
        <label>
          Точка
          <select />
        </label>
      ),
      active: false,
      onClear: vi.fn(),
    },
  ];
}

describe("ConfigurableFilterBar", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("shows the standard layout and lets the user add an optional filter", async () => {
    render(
      <ConfigurableFilterBar preferenceKey="test" filters={filters()} onResetValues={vi.fn()} />,
    );

    expect(screen.getByLabelText("Поиск")).toBeInTheDocument();
    expect(screen.getByLabelText("Статус")).toBeInTheDocument();
    expect(screen.queryByLabelText("Точка")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /^Фильтры/ }));
    const branchToggle = screen.getByRole("checkbox", { name: "Точка" });
    await waitFor(() => {
      expect(screen.getByRole("checkbox", { name: /^Статус/ })).toHaveFocus();
    });
    fireEvent.click(branchToggle);
    fireEvent.click(screen.getByRole("button", { name: /^Фильтры/ }));

    expect(screen.getByLabelText("Точка")).toBeInTheDocument();
    expect(window.localStorage.getItem("aurum:filter-layout:v1:test")).toBe(
      '["search","status","branch"]',
    );
  });

  it("clears an active filter and restores focus when it is hidden", async () => {
    const onClear = vi.fn();
    render(
      <ConfigurableFilterBar
        preferenceKey="test"
        filters={filters(onClear)}
        onResetValues={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Убрать фильтр «Статус»" }));

    expect(onClear).toHaveBeenCalledTimes(1);
    expect(screen.queryByLabelText("Статус")).not.toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /^Фильтры/ })).toHaveFocus();
    });
  });

  it("stores only field identifiers and restores the saved layout", () => {
    window.localStorage.setItem(
      "aurum:filter-layout:v1:test",
      JSON.stringify(["search", "branch"]),
    );

    render(
      <ConfigurableFilterBar preferenceKey="test" filters={filters()} onResetValues={vi.fn()} />,
    );

    expect(screen.getByLabelText("Поиск")).toBeInTheDocument();
    expect(screen.getByLabelText("Точка")).toBeInTheDocument();
    expect(screen.queryByLabelText("Статус")).not.toBeInTheDocument();
  });

  it("resets active values independently from the visible layout", () => {
    const onReset = vi.fn();
    render(
      <ConfigurableFilterBar preferenceKey="test" filters={filters()} onResetValues={onReset} />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Сбросить (1)" }));
    expect(onReset).toHaveBeenCalledTimes(1);
    expect(screen.getByLabelText("Статус")).toBeInTheDocument();
  });

  it("ignores a damaged stored preference", () => {
    window.localStorage.setItem("aurum:filter-layout:v1:test", "{broken");

    render(
      <ConfigurableFilterBar preferenceKey="test" filters={filters()} onResetValues={vi.fn()} />,
    );

    expect(screen.getByLabelText("Поиск")).toBeInTheDocument();
    expect(screen.getByLabelText("Статус")).toBeInTheDocument();
    expect(screen.queryByLabelText("Точка")).not.toBeInTheDocument();
  });

  it("loads a separate layout when the account or tenant scope changes", () => {
    window.localStorage.setItem(
      "aurum:filter-layout:v1:user-a:tenant-a:test",
      JSON.stringify(["search"]),
    );
    window.localStorage.setItem(
      "aurum:filter-layout:v1:user-b:tenant-b:test",
      JSON.stringify(["search", "branch"]),
    );

    const view = render(
      <ConfigurableFilterBar
        preferenceKey="user-a:tenant-a:test"
        filters={filters()}
        onResetValues={vi.fn()}
      />,
    );
    expect(screen.queryByLabelText("Точка")).not.toBeInTheDocument();

    view.rerender(
      <ConfigurableFilterBar
        preferenceKey="user-b:tenant-b:test"
        filters={filters()}
        onResetValues={vi.fn()}
      />,
    );
    expect(screen.getByLabelText("Точка")).toBeInTheDocument();
  });
});
