import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
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
      activeLabel: "Статус: Активен",
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

function StatefulFilters() {
  const [status, setStatus] = useState("");
  return (
    <ConfigurableFilterBar
      preferenceKey="stateful"
      onResetValues={() => setStatus("")}
      filters={[
        {
          id: "status",
          label: "Статус",
          activeLabel: `Статус: ${status}`,
          active: status !== "",
          onClear: () => setStatus(""),
          content: (
            <label>
              Статус
              <select value={status} onChange={(event) => setStatus(event.target.value)}>
                <option value="">Все</option>
                <option>Активен</option>
              </select>
            </label>
          ),
        },
      ]}
    />
  );
}

describe("ConfigurableFilterBar", () => {
  beforeEach(() => window.localStorage.clear());

  it("describes each chip with its own selected value across multiple toolbars", () => {
    render(
      <>
        <ConfigurableFilterBar preferenceKey="first" filters={filters()} onResetValues={vi.fn()} />
        <ConfigurableFilterBar
          preferenceKey="second"
          filters={filters().map((filter) =>
            filter.id === "status" ? { ...filter, activeLabel: "Статус: Архив" } : filter,
          )}
          onResetValues={vi.fn()}
        />
      </>,
    );
    const chips = screen.getAllByRole("button", { name: "Сбросить фильтр «Статус»" });
    expect(chips[0]).toHaveAccessibleDescription("Статус: Активен");
    expect(chips[1]).toHaveAccessibleDescription("Статус: Архив");
    expect(chips[0]?.getAttribute("aria-describedby")).not.toBe(
      chips[1]?.getAttribute("aria-describedby"),
    );
  });

  it("announces pending report changes and removes the notice when they are applied", () => {
    const onResetValues = vi.fn();
    const view = render(
      <ConfigurableFilterBar
        preferenceKey="report"
        filters={filters()}
        onResetValues={onResetValues}
      />,
    );
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    view.rerender(
      <ConfigurableFilterBar
        preferenceKey="report"
        filters={filters()}
        onResetValues={onResetValues}
        pendingChangesMessage="Условия изменены. Нажмите «Показать», чтобы обновить отчёт."
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent(
      "Условия изменены. Нажмите «Показать», чтобы обновить отчёт.",
    );
    view.rerender(
      <ConfigurableFilterBar
        preferenceKey="report"
        filters={filters()}
        onResetValues={onResetValues}
      />,
    );
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("keeps search in the toolbar and exposes every optional condition in the panel", () => {
    render(
      <ConfigurableFilterBar preferenceKey="test" filters={filters()} onResetValues={vi.fn()} />,
    );
    expect(screen.getByLabelText("Поиск")).toBeInTheDocument();
    expect(screen.queryByLabelText("Статус")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /^Фильтры/ }));
    const dialog = screen.getByRole("dialog", { name: "Фильтры" });
    expect(within(dialog).getByLabelText("Статус")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("Точка")).toBeInTheDocument();
    expect(within(dialog).queryByLabelText("Поиск")).not.toBeInTheDocument();
    expect(within(dialog).queryByRole("checkbox")).not.toBeInTheDocument();
  });

  it.each([JSON.stringify(["search"]), "{broken"])(
    "ignores old layout preferences: %s",
    (stored) => {
      window.localStorage.setItem("aurum:filter-layout:v1:test", stored);
      render(
        <ConfigurableFilterBar preferenceKey="test" filters={filters()} onResetValues={vi.fn()} />,
      );
      fireEvent.click(screen.getByRole("button", { name: /^Фильтры/ }));
      expect(screen.getByLabelText("Статус")).toBeInTheDocument();
      expect(screen.getByLabelText("Точка")).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "Готово" }));
      expect(window.localStorage.getItem("aurum:filter-layout:v1:test")).toBe(stored);
    },
  );

  it.each(["Готово", "Закрыть", "Escape"])(
    "retains a changed value after closing with %s",
    (close) => {
      render(<StatefulFilters />);
      const trigger = screen.getByRole("button", { name: /^Фильтры/ });
      trigger.focus();
      fireEvent.click(trigger);
      expect(document.body.style.overflow).toBe("hidden");
      fireEvent.change(screen.getByLabelText("Статус"), { target: { value: "Активен" } });
      if (close === "Escape") fireEvent.keyDown(window, { key: "Escape" });
      else fireEvent.click(screen.getByRole("button", { name: close }));
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      expect(trigger).toHaveFocus();
      expect(document.body.style.overflow).not.toBe("hidden");
      expect(screen.getByRole("button", { name: "Сбросить фильтр «Статус»" })).toHaveTextContent(
        "Статус: Активен",
      );
      fireEvent.click(trigger);
      expect(screen.getByLabelText("Статус")).toHaveValue("Активен");
    },
  );

  it("clears one condition through its chip and resets all values from the panel", () => {
    render(<StatefulFilters />);
    const open = () => fireEvent.click(screen.getByRole("button", { name: /^Фильтры/ }));
    open();
    fireEvent.change(screen.getByLabelText("Статус"), { target: { value: "Активен" } });
    fireEvent.click(screen.getByRole("button", { name: "Готово" }));
    fireEvent.click(screen.getByRole("button", { name: "Сбросить фильтр «Статус»" }));
    expect(screen.getByRole("button", { name: /^Фильтры/ })).toHaveFocus();
    expect(
      screen.queryByRole("button", { name: "Сбросить фильтр «Статус»" }),
    ).not.toBeInTheDocument();
    open();
    fireEvent.change(screen.getByLabelText("Статус"), { target: { value: "Активен" } });
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", { name: "Сбросить (1)" }),
    );
    expect(screen.getByLabelText("Статус")).toHaveValue("");
    expect(
      within(screen.getByRole("dialog")).getByRole("button", { name: "Сбросить" }),
    ).toBeDisabled();
  });

  it("traps keyboard focus inside the panel including its footer", () => {
    render(
      <ConfigurableFilterBar preferenceKey="test" filters={filters()} onResetValues={vi.fn()} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /^Фильтры/ }));
    const close = screen.getByRole("button", { name: "Закрыть" });
    const done = screen.getByRole("button", { name: "Готово" });
    expect(close).toHaveFocus();
    fireEvent.keyDown(close, { key: "Tab", shiftKey: true });
    expect(done).toHaveFocus();
    fireEvent.keyDown(done, { key: "Tab" });
    expect(close).toHaveFocus();
  });

  it("closes and unlocks the panel when the account or tenant scope changes", () => {
    const view = render(
      <ConfigurableFilterBar
        preferenceKey="user-a:tenant-a"
        filters={filters()}
        onResetValues={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /^Фильтры/ }));
    view.rerender(
      <ConfigurableFilterBar
        preferenceKey="user-b:tenant-b"
        filters={filters()}
        onResetValues={vi.fn()}
      />,
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(document.body.style.overflow).not.toBe("hidden");
  });

  it("clears a newly unavailable active filter and removes its field and chip", async () => {
    const onClear = vi.fn();
    const view = render(
      <ConfigurableFilterBar
        preferenceKey="test"
        filters={filters(onClear)}
        onResetValues={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /^Фильтры/ }));
    const restricted = filters(onClear).map((filter) =>
      filter.id === "status" ? { ...filter, available: false } : filter,
    );
    view.rerender(
      <ConfigurableFilterBar preferenceKey="test" filters={restricted} onResetValues={vi.fn()} />,
    );
    await waitFor(() => expect(onClear).toHaveBeenCalledTimes(1));
    expect(screen.queryByLabelText("Статус")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Сбросить фильтр «Статус»" }),
    ).not.toBeInTheDocument();
    expect(screen.getByLabelText("Точка")).toBeInTheDocument();
  });
});
