import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { ConfirmDialog, Modal } from "@/components/ui";

describe("ConfirmDialog", () => {
  it("renders nothing while closed", () => {
    const { container } = render(
      <ConfirmDialog
        open={false}
        title="Заголовок"
        message="Текст"
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("shows title, message and the action labels when open", () => {
    render(
      <ConfirmDialog
        open
        title="Архивировать позицию"
        message="Вернуть можно будет позже."
        confirmLabel="Архивировать"
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.getByText("Архивировать позицию")).toBeInTheDocument();
    expect(screen.getByText("Вернуть можно будет позже.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Архивировать" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Отмена" })).toBeInTheDocument();
  });

  it("fires onConfirm and onCancel", () => {
    const onConfirm = vi.fn();
    const onCancel = vi.fn();
    render(
      <ConfirmDialog
        open
        title="Удалить"
        message="Точно?"
        confirmLabel="Удалить"
        variant="danger"
        onConfirm={onConfirm}
        onCancel={onCancel}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Удалить" }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Отмена" }));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("moves focus into the dialog and traps Tab navigation", () => {
    render(
      <ConfirmDialog
        open
        title="Confirm action"
        message="This cannot be undone."
        confirmLabel="Confirm"
        cancelLabel="Cancel"
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    const dialog = screen.getByRole("dialog", { name: "Confirm action" });
    const buttons = Array.from(dialog.querySelectorAll("button"));
    const firstButton = buttons[0];
    const lastButton = buttons.at(-1);

    expect(firstButton).toHaveFocus();

    lastButton?.focus();
    fireEvent.keyDown(window, { key: "Tab" });
    expect(firstButton).toHaveFocus();

    firstButton?.focus();
    fireEvent.keyDown(window, { key: "Tab", shiftKey: true });
    expect(lastButton).toHaveFocus();
  });

  it("restores focus to the opener when closed", () => {
    function Harness(): JSX.Element {
      const [open, setOpen] = useState(false);
      return (
        <>
          <button type="button" onClick={() => setOpen(true)}>
            Open dialog
          </button>
          <ConfirmDialog
            open={open}
            title="Confirm action"
            message="This cannot be undone."
            confirmLabel="Confirm"
            cancelLabel="Cancel"
            onConfirm={vi.fn()}
            onCancel={() => setOpen(false)}
          />
        </>
      );
    }

    render(<Harness />);

    const opener = screen.getByRole("button", { name: "Open dialog" });
    opener.focus();
    fireEvent.click(opener);
    expect(screen.getByRole("dialog", { name: "Confirm action" })).toBeInTheDocument();

    fireEvent.keyDown(window, { key: "Escape" });
    expect(opener).toHaveFocus();
  });

  it("closes only the top dialog when dialogs are nested", () => {
    function Harness(): JSX.Element {
      const [confirmOpen, setConfirmOpen] = useState(true);
      return (
        <Modal open onClose={vi.fn()} title="Назначения">
          <ConfirmDialog
            open={confirmOpen}
            title="Отозвать роль"
            message="Роль перестанет действовать."
            onConfirm={vi.fn()}
            onCancel={() => setConfirmOpen(false)}
          />
        </Modal>
      );
    }

    render(<Harness />);
    expect(screen.getByRole("dialog", { name: "Назначения" })).toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "Отозвать роль" })).toBeInTheDocument();

    fireEvent.keyDown(window, { key: "Escape" });

    expect(screen.queryByRole("dialog", { name: "Отозвать роль" })).not.toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "Назначения" })).toBeInTheDocument();
  });
});
