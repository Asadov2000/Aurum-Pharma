import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ActionMenu } from "@/components/ui/ActionMenu";

describe("ActionMenu", () => {
  afterEach(() => vi.restoreAllMocks());

  const commands = [
    { id: "open", label: "Открыть", onSelect: vi.fn() },
    { id: "copy", label: "Скопировать", onSelect: vi.fn() },
  ];
  function Region({ enabled = true }: { enabled?: boolean }) {
    return (
      <article data-context-actions tabIndex={0} data-testid="region">
        <span>Товар</span>
        <a href="/catalog">Ссылка</a>
        <input aria-label="Поле" />
        <textarea aria-label="Текст" />
        <select aria-label="Выбор">
          <option>Один</option>
        </select>
        <span contentEditable suppressContentEditableWarning>
          Редактируемый текст
        </span>
        <ActionMenu
          label="Действия"
          items={commands}
          contextTarget="[data-context-actions]"
          contextEnabled={enabled}
        />
      </article>
    );
  }

  it("opens at the pointer without running a command and restores keyboard focus", async () => {
    render(<Region />);
    const region = screen.getByTestId("region");
    expect(fireEvent.contextMenu(region, { clientX: 200, clientY: 150 })).toBe(false);
    expect(screen.getByRole("menu")).toHaveStyle({ left: "200px", top: "150px" });
    expect(commands[0].onSelect).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.getByRole("menuitem", { name: "Открыть" })).toHaveFocus());
    fireEvent.keyDown(document.activeElement!, { key: "ArrowDown" });
    expect(screen.getByRole("menuitem", { name: "Скопировать" })).toHaveFocus();
    fireEvent.keyDown(document.activeElement!, { key: "Home" });
    expect(screen.getByRole("menuitem", { name: "Открыть" })).toHaveFocus();
    fireEvent.keyDown(document.activeElement!, { key: "Escape" });
    await waitFor(() => expect(region).toHaveFocus());
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    fireEvent.keyDown(region, { key: "F10", shiftKey: true });
    expect(screen.getByRole("menu")).toBeInTheDocument();
  });

  it("preserves native menus for editable controls, links and selected text", () => {
    render(<Region />);
    for (const element of [
      screen.getByRole("link"),
      screen.getByLabelText("Поле"),
      screen.getByLabelText("Текст"),
      screen.getByLabelText("Выбор"),
      screen.getByText("Редактируемый текст"),
    ]) {
      expect(fireEvent.contextMenu(element)).toBe(true);
      expect(fireEvent.keyDown(element, { key: "F10", shiftKey: true })).toBe(true);
    }
    const selection = document.getSelection()!;
    vi.spyOn(selection, "toString").mockReturnValue("Выделено");
    expect(fireEvent.contextMenu(screen.getByTestId("region"))).toBe(true);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("keeps the visible actions button available when right-click is disabled", () => {
    render(<Region enabled={false} />);
    expect(fireEvent.contextMenu(screen.getByTestId("region"))).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Действия" }));
    expect(screen.getByRole("menu")).toBeInTheDocument();
  });

  it("does not bubble portal commands to the record click handler and opens only one menu", () => {
    const onRecord = vi.fn();
    const onSelect = vi.fn();
    render(
      <>
        <article onClick={onRecord}>
          <ActionMenu label="Первый" items={[{ label: "Выполнить", onSelect }]} />
        </article>
        <ActionMenu label="Второй" items={commands} />
      </>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Первый" }));
    onRecord.mockClear();
    fireEvent.click(screen.getByRole("menuitem", { name: "Выполнить" }));
    expect(onSelect).toHaveBeenCalledOnce();
    expect(onRecord).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Первый" }));
    fireEvent.click(screen.getByRole("button", { name: "Второй" }));
    expect(screen.getAllByRole("menu")).toHaveLength(1);
    expect(screen.getByRole("menu")).toHaveAccessibleName("Второй");
  });

  it("closes stale menus when permissions, record identity or loading state changes", () => {
    const { rerender } = render(<ActionMenu label="Действия" items={commands} resetKey="first" />);
    const trigger = screen.getByRole("button", { name: "Действия" });
    fireEvent.click(trigger);
    rerender(<ActionMenu label="Действия" items={[commands[0]]} resetKey="first" />);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    fireEvent.click(trigger);
    rerender(<ActionMenu label="Действия" items={[commands[0]]} resetKey="second" />);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    fireEvent.click(trigger);
    rerender(<ActionMenu label="Действия" items={[commands[0]]} resetKey="second" isLoading />);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(trigger).toBeDisabled();
  });

  it("clamps near viewport edges and allows scrolling inside the menu", async () => {
    render(<Region />);
    fireEvent.contextMenu(screen.getByTestId("region"), { clientX: 9999, clientY: 9999 });
    const menu = screen.getByRole("menu");
    expect(parseFloat(menu.style.left)).toBeLessThan(window.innerWidth);
    expect(parseFloat(menu.style.top)).toBeLessThan(window.innerHeight);
    await waitFor(() => expect(screen.getByRole("menuitem", { name: "Открыть" })).toHaveFocus());
    await new Promise((resolve) => requestAnimationFrame(resolve));
    fireEvent.scroll(menu);
    expect(menu).toBeInTheDocument();
    fireEvent.scroll(window);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });
  it("closes an open menu when disabled and requires a fresh opening after re-enabling", () => {
    const onSelect = vi.fn();
    const items = [{ label: "Приостановить", onSelect }];
    const { rerender } = render(<ActionMenu label="Действия сотрудника" items={items} />);
    const trigger = screen.getByRole("button", { name: "Действия сотрудника" });
    fireEvent.click(trigger);
    const staleAction = screen.getByRole("menuitem", { name: "Приостановить" });
    expect(trigger).toHaveAttribute("aria-expanded", "true");

    rerender(<ActionMenu label="Действия сотрудника" items={items} disabled />);

    expect(trigger).toBeDisabled();
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    fireEvent.click(staleAction);
    fireEvent.click(trigger);
    fireEvent.keyDown(trigger, { key: "Enter" });
    expect(onSelect).not.toHaveBeenCalled();
    expect(screen.queryByRole("menuitem")).not.toBeInTheDocument();

    rerender(<ActionMenu label="Действия сотрудника" items={items} />);

    expect(trigger).toBeEnabled();
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    fireEvent.click(trigger);
    fireEvent.click(screen.getByRole("menuitem", { name: "Приостановить" }));
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });
});
