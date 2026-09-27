import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ActionMenu } from "@/components/ui/ActionMenu";

describe("ActionMenu", () => {
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
