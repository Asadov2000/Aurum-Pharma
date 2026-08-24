import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/features/settings/queries", () => ({
  useUserPreferencesQuery: () => ({ data: undefined }),
  useUpdateUserPreferences: () => ({ isPending: false, mutate: vi.fn() }),
}));

import { AppearanceMenu } from "@/components/AppearanceMenu";

describe("AppearanceMenu", () => {
  beforeEach(() => {
    window.localStorage.clear();
    document.documentElement.setAttribute("data-theme", "light");
    document.documentElement.setAttribute("data-density", "comfortable");
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: vi.fn().mockImplementation((query: string) => ({
        addEventListener: vi.fn(),
        matches: false,
        media: query,
        removeEventListener: vi.fn(),
      })),
    });
  });

  it("changes theme and density without storing account data", () => {
    render(<AppearanceMenu />);

    fireEvent.click(screen.getByRole("button", { name: "Вид интерфейса" }));
    expect(screen.getByRole("dialog", { name: "Вид интерфейса" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Тёмная" }));
    fireEvent.click(screen.getByRole("button", { name: "Сенсор" }));

    expect(window.localStorage.getItem("theme:preference")).toBe("dark");
    expect(window.localStorage.getItem("ui:density")).toBe("touch");
    expect(document.documentElement).toHaveAttribute("data-theme", "dark");
    expect(document.documentElement).toHaveAttribute("data-density", "touch");
    const storedKeys = Array.from({ length: window.localStorage.length }, (_, index) => {
      return window.localStorage.key(index);
    }).sort();
    expect(storedKeys).toEqual(["theme:preference", "ui:density"]);
  });

  it("closes on Escape and restores focus to the trigger", async () => {
    render(<AppearanceMenu />);
    const trigger = screen.getByRole("button", { name: "Вид интерфейса" });

    fireEvent.click(trigger);
    fireEvent.keyDown(document, { key: "Escape" });

    expect(screen.queryByRole("dialog", { name: "Вид интерфейса" })).not.toBeInTheDocument();
    await waitFor(() => expect(trigger).toHaveFocus());
  });
});
