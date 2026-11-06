import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const preferenceState = vi.hoisted(() => ({
  enqueue: vi.fn(),
  retry: vi.fn(),
  hasPending: false,
  error: null as Error | null,
  preferences: { data: undefined, error: null as Error | null },
}));
const setTheme = vi.hoisted(() => vi.fn());
vi.mock("@/features/settings/usePreferenceAutosave", () => ({
  usePreferenceAutosave: () => preferenceState,
}));
vi.mock("@/lib/themeTransition", () => ({ setThemePreferenceWithTransition: setTheme }));

import { PosWorkstationSettings } from "@/features/pos/PosWorkstationSettings";
import { type PosModePref } from "@/features/pos/usePosMode";

function Harness(): JSX.Element {
  const [open, setOpen] = useState(false);
  const [pref, setPref] = useState<PosModePref>("auto");
  const [sound, setSound] = useState(false);
  return (
    <>
      <button onClick={() => setOpen(true)}>Настройки рабочего места</button>
      <p>Текущий чек: 3 штуки · 3,00 сомони</p>
      <PosWorkstationSettings
        open={open}
        onClose={() => setOpen(false)}
        pref={pref}
        setPref={setPref}
        touch={pref === "touch"}
        soundOn={sound}
        onSoundChange={setSound}
      />
    </>
  );
}

async function openSettings() {
  const trigger = screen.getByRole("button", { name: "Настройки рабочего места" });
  trigger.focus();
  fireEvent.click(trigger);
  return screen.findByRole("dialog", { name: "Настройки рабочего места" });
}

describe("PosWorkstationSettings", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    preferenceState.hasPending = false;
    preferenceState.error = null;
    preferenceState.preferences.error = null;
  });

  it("keeps device controls behind the dialog and preserves the current workspace", async () => {
    render(<Harness />);
    expect(screen.queryByRole("group", { name: "Режим кассы" })).not.toBeInTheDocument();
    const dialog = await openSettings();
    fireEvent.click(within(dialog).getByRole("button", { name: "Сенсор" }));
    expect(within(dialog).getByRole("button", { name: "Сенсор" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    fireEvent.click(within(dialog).getByRole("checkbox", { name: "Звук сканера" }));
    expect(within(dialog).getByRole("checkbox", { name: "Звук сканера" })).toBeChecked();
    fireEvent.click(within(dialog).getByRole("button", { name: "Готово" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByText("Текущий чек: 3 штуки · 3,00 сомони")).toBeInTheDocument();
    const reopened = await openSettings();
    expect(within(reopened).getByRole("checkbox", { name: "Звук сканера" })).toBeChecked();
    expect(within(reopened).getByRole("button", { name: "Сенсор" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("uses the shared theme preference queue and leaves the dialog open", async () => {
    render(<Harness />);
    const dialog = await openSettings();
    fireEvent.click(within(dialog).getByRole("button", { name: "Тёмная" }));
    expect(setTheme).toHaveBeenCalledWith("dark");
    expect(preferenceState.enqueue).toHaveBeenCalledWith({ theme: "dark" });
    expect(within(dialog).getByRole("button", { name: "Тёмная" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(dialog).toBeInTheDocument();
  });

  it("shows unsynchronized theme state with a retry without blocking device controls", async () => {
    preferenceState.error = new Error("offline");
    render(<Harness />);
    const dialog = await openSettings();
    expect(within(dialog).getByRole("status")).toHaveTextContent("пока не синхронизирована");
    fireEvent.click(within(dialog).getByRole("button", { name: "Повторить синхронизацию" }));
    expect(preferenceState.retry).toHaveBeenCalledOnce();
    fireEvent.click(within(dialog).getByRole("button", { name: "Клавиши" }));
    expect(within(dialog).getByRole("button", { name: "Клавиши" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("closes with Escape and returns focus to the trigger", async () => {
    render(<Harness />);
    await openSettings();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Настройки рабочего места" })).toHaveFocus(),
    );
  });
});
