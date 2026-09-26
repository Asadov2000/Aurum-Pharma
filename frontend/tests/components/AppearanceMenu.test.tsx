import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { type UserPreferences } from "@/features/settings/types";

const state = vi.hoisted(() => ({
  preferences: undefined as UserPreferences | undefined,
  mutateAsync: vi.fn(),
  refetch: vi.fn(),
}));

vi.mock("@/features/auth/hooks", () => ({
  useAuth: () => ({ user: { id: "appearance-user", active_tenant_id: "appearance-tenant" } }),
}));

vi.mock("@/features/settings/queries", () => ({
  useUserPreferencesQuery: () => ({ data: state.preferences, refetch: state.refetch }),
  useUpdateUserPreferences: () => ({ isPending: false, mutateAsync: state.mutateAsync }),
}));

import { AppearanceMenu } from "@/components/AppearanceMenu";

const PENDING_KEY =
  "aurum:preferences-pending:v1:appearance-user%3Aappearance-tenant:header-appearance";
const SERVER_PREFERENCES: UserPreferences = {
  theme: "light",
  density: "comfortable",
  contrast: "standard",
  reduce_motion: false,
  accent: "teal",
  workspace: {
    desktop_mode: "auto",
    hidden_routes: [],
    favorite_routes: [],
    route_order: [],
    start_route: "/",
  },
  version: 7,
  updated_at: "2026-09-26T09:00:00Z",
};

describe("AppearanceMenu", () => {
  beforeEach(() => {
    state.preferences = undefined;
    state.mutateAsync.mockReset();
    state.refetch.mockReset();
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

  afterEach(() => {
    window.localStorage.clear();
  });

  it("changes theme and density while persisting only appearance preferences", () => {
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
    expect(storedKeys).toEqual([PENDING_KEY, "theme:preference", "ui:density"]);
    expect(JSON.parse(window.localStorage.getItem(PENDING_KEY) ?? "{}")).toEqual({
      theme: "dark",
      density: "touch",
    });
    expect(state.mutateAsync).not.toHaveBeenCalled();
  });

  it("keeps early choices when initial server preferences arrive and then saves them", async () => {
    let confirmSave!: (value: UserPreferences) => void;
    state.mutateAsync.mockReturnValue(
      new Promise<UserPreferences>((resolve) => {
        confirmSave = resolve;
      }),
    );
    const { rerender } = render(<AppearanceMenu />);
    fireEvent.click(screen.getByRole("button", { name: "Вид интерфейса" }));
    fireEvent.click(screen.getByRole("button", { name: "Тёмная" }));
    fireEvent.click(screen.getByRole("button", { name: "Сенсор" }));

    // The real autosave queue must wait for the initial server version.
    expect(state.mutateAsync).not.toHaveBeenCalled();
    expect(document.documentElement).toHaveAttribute("data-density", "touch");
    expect(document.documentElement).toHaveAttribute("data-theme", "dark");

    state.preferences = SERVER_PREFERENCES;
    rerender(<AppearanceMenu />);

    expect(screen.getByRole("button", { name: "Сенсор" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Тёмная" })).toHaveAttribute("aria-pressed", "true");
    expect(document.documentElement).toHaveAttribute("data-density", "touch");
    expect(document.documentElement).toHaveAttribute("data-theme", "dark");
    await waitFor(() =>
      expect(state.mutateAsync).toHaveBeenCalledExactlyOnceWith({
        expected_version: 7,
        theme: "dark",
        density: "touch",
      }),
    );
    expect(window.localStorage.getItem(PENDING_KEY)).not.toBeNull();

    await act(async () => {
      state.preferences = { ...SERVER_PREFERENCES, theme: "dark", density: "touch", version: 8 };
      confirmSave(state.preferences);
      rerender(<AppearanceMenu />);
    });

    await waitFor(() => expect(window.localStorage.getItem(PENDING_KEY)).toBeNull());
    expect(state.mutateAsync).toHaveBeenCalledTimes(1);
    expect(document.documentElement).toHaveAttribute("data-density", "touch");
    expect(document.documentElement).toHaveAttribute("data-theme", "dark");
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
