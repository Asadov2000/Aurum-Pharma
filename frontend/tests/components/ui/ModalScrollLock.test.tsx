import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { StrictMode, useState } from "react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { Modal } from "@/components/ui/Modal";

const noop = () => undefined;

function SiblingDialogs({
  primaryOpen,
  confirmationOpen,
}: {
  primaryOpen: boolean;
  confirmationOpen: boolean;
}) {
  return (
    <>
      <Modal open={primaryOpen} onClose={noop} title="Профиль сотрудника">
        Сведения о сотруднике
      </Modal>
      <ConfirmDialog
        open={confirmationOpen}
        title="Подтверждение действия"
        message="Подтвердите действие с сотрудником"
        onConfirm={noop}
        onCancel={noop}
      />
    </>
  );
}

function EmployeeAction() {
  const [primaryOpen, setPrimaryOpen] = useState(true);
  const [confirmationOpen, setConfirmationOpen] = useState(false);
  return (
    <>
      <Modal open={primaryOpen} onClose={() => setPrimaryOpen(false)} title="Профиль сотрудника">
        <button onClick={() => setConfirmationOpen(true)}>Завершить действие</button>
      </Modal>
      <ConfirmDialog
        open={confirmationOpen}
        title="Подтверждение действия"
        message="После подтверждения оба окна закроются"
        onCancel={() => setConfirmationOpen(false)}
        onConfirm={() => {
          setPrimaryOpen(false);
          setConfirmationOpen(false);
        }}
      />
    </>
  );
}

let originalStyle: string | null;

beforeEach(() => {
  originalStyle = document.body.getAttribute("style");
  document.body.style.removeProperty("overflow");
});

afterEach(() => {
  // Unmount first so effect cleanup cannot overwrite the restored test boundary.
  cleanup();
  if (originalStyle === null) document.body.removeAttribute("style");
  else document.body.setAttribute("style", originalStyle);
});

describe("scroll locking across real sibling dialogs", () => {
  it("unlocks after one confirmed employee action closes both sibling windows", () => {
    render(<EmployeeAction />);
    fireEvent.click(screen.getByRole("button", { name: "Завершить действие" }));
    expect(screen.getAllByRole("dialog")).toHaveLength(2);
    expect(document.body.style.overflow).toBe("hidden");

    fireEvent.click(screen.getByRole("button", { name: "Подтвердить" }));

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(document.body.style.overflow).toBe("");
  });

  it.each(["primary", "confirmation"] as const)(
    "keeps scrolling locked until the last window closes (%s closes first)",
    (firstClosed) => {
      const { rerender } = render(<SiblingDialogs primaryOpen confirmationOpen={false} />);
      rerender(<SiblingDialogs primaryOpen confirmationOpen />);

      rerender(
        <SiblingDialogs
          primaryOpen={firstClosed !== "primary"}
          confirmationOpen={firstClosed !== "confirmation"}
        />,
      );
      expect(screen.getAllByRole("dialog")).toHaveLength(1);
      expect(document.body.style.overflow).toBe("hidden");

      rerender(<SiblingDialogs primaryOpen={false} confirmationOpen={false} />);
      expect(document.body.style.overflow).toBe("");
    },
  );

  it("unlocks when navigation unmounts both open sibling windows", () => {
    const { unmount } = render(<SiblingDialogs primaryOpen confirmationOpen />);
    expect(document.body.style.overflow).toBe("hidden");

    unmount();

    expect(document.body.style.overflow).toBe("");
  });

  it("restores scrolling after StrictMode effect replay and unmount", () => {
    const { unmount } = render(
      <StrictMode>
        <SiblingDialogs primaryOpen confirmationOpen />
      </StrictMode>,
    );
    expect(document.body.style.overflow).toBe("hidden");

    unmount();

    expect(document.body.style.overflow).toBe("");
  });

  it.each(["auto", "scroll", "hidden"])("restores the original inline overflow=%s", (overflow) => {
    document.body.style.overflow = overflow;
    const { rerender } = render(<SiblingDialogs primaryOpen confirmationOpen={false} />);
    rerender(<SiblingDialogs primaryOpen confirmationOpen />);
    expect(document.body.style.overflow).toBe("hidden");

    rerender(<SiblingDialogs primaryOpen={false} confirmationOpen={false} />);

    expect(document.body.style.overflow).toBe(overflow);
  });
});
