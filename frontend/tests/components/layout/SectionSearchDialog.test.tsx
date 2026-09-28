import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import SectionSearchDialog from "@/components/layout/SectionSearchDialog";

const ITEMS = [
  { to: "/catalog", label: "Каталог" },
  { to: "/reports", label: "Отчёты" },
  { to: "/incoming", label: "Приёмка товаров" },
] as const;

describe("SectionSearchDialog", () => {
  it("shows only supplied sections and focuses the search field", () => {
    render(<SectionSearchDialog open items={ITEMS} onClose={vi.fn()} onSelect={vi.fn()} />);

    expect(screen.getByRole("dialog", { name: "Перейти в раздел" })).toBeInTheDocument();
    expect(screen.getByRole("searchbox", { name: "Найти раздел" })).toHaveFocus();
    expect(screen.getByRole("button", { name: "Каталог" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Отчёты" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Роли" })).not.toBeInTheDocument();
  });

  it.each([
    ["  ОТЧЕТ  ", "Отчёты"],
    ["ПРИЁМКА", "Приёмка товаров"],
    ["приемка", "Приёмка товаров"],
  ])("matches a Russian query %s", (query, label) => {
    render(<SectionSearchDialog open items={ITEMS} onClose={vi.fn()} onSelect={vi.fn()} />);

    fireEvent.change(screen.getByRole("searchbox", { name: "Найти раздел" }), {
      target: { value: query },
    });

    expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Каталог" })).not.toBeInTheDocument();
  });

  it("shows an empty state and restores results after clearing the query", () => {
    render(<SectionSearchDialog open items={ITEMS} onClose={vi.fn()} onSelect={vi.fn()} />);
    const input = screen.getByRole("searchbox", { name: "Найти раздел" });

    fireEvent.change(input, { target: { value: "сотрудники" } });

    expect(screen.getByRole("status")).toHaveTextContent("Разделы не найдены");
    expect(screen.queryByRole("button", { name: "Каталог" })).not.toBeInTheDocument();

    fireEvent.change(input, { target: { value: "" } });

    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Каталог" })).toBeInTheDocument();
  });

  it("passes the selected route to the owner callback", () => {
    const onSelect = vi.fn();
    render(<SectionSearchDialog open items={ITEMS} onClose={vi.fn()} onSelect={onSelect} />);

    fireEvent.click(screen.getByRole("button", { name: "Отчёты" }));

    expect(onSelect).toHaveBeenCalledExactlyOnceWith("/reports");
  });

  it.each(["escape", "button"] as const)(
    "closes through %s, restores trigger focus and clears the next search",
    (closeMethod) => {
      const onClose = vi.fn();

      function Harness(): JSX.Element {
        const [open, setOpen] = useState(false);
        return (
          <>
            <button type="button" onClick={() => setOpen(true)}>
              Поиск разделов
            </button>
            <SectionSearchDialog
              open={open}
              items={ITEMS}
              onClose={() => {
                onClose();
                setOpen(false);
              }}
              onSelect={vi.fn()}
            />
          </>
        );
      }

      render(<Harness />);
      const trigger = screen.getByRole("button", { name: "Поиск разделов" });
      trigger.focus();
      fireEvent.click(trigger);
      fireEvent.change(screen.getByRole("searchbox", { name: "Найти раздел" }), {
        target: { value: "отчет" },
      });

      if (closeMethod === "escape") fireEvent.keyDown(window, { key: "Escape" });
      else fireEvent.click(screen.getByRole("button", { name: "Закрыть" }));

      expect(onClose).toHaveBeenCalledOnce();
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      expect(trigger).toHaveFocus();

      fireEvent.click(trigger);

      expect(screen.getByRole("searchbox", { name: "Найти раздел" })).toHaveValue("");
      expect(screen.getByRole("button", { name: "Каталог" })).toBeInTheDocument();
    },
  );
});
