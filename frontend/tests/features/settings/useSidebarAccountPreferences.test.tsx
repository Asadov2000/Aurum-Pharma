import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, waitFor } from "@testing-library/react";
import { type ComponentType, type PropsWithChildren } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  defaultSidebarPreferences,
  loadSidebarPreferences,
  saveSidebarPreferences,
} from "@/components/layout/sidebarPreferences";
import { settingsKeys } from "@/features/settings/queries";
import { type UserPreferences, type UserPreferencesUpdate } from "@/features/settings/types";

const state = vi.hoisted(() => ({
  generation: 1,
  user: { id: "user-1", active_tenant_id: "tenant-1" } as {
    id: string;
    active_tenant_id: string;
  } | null,
  getPreferences: vi.fn(),
  updatePreferences: vi.fn(),
}));

vi.mock("@/features/auth/hooks", () => ({ useAuth: () => ({ user: state.user }) }));
vi.mock("@/stores/auth", () => ({
  getAuthSessionGeneration: () => state.generation,
  useAuthStore: { getState: () => ({ user: state.user }) },
}));
vi.mock("@/features/settings/api", () => ({
  getUserPreferences: () => state.getPreferences(),
  updateUserPreferences: (payload: UserPreferencesUpdate) => state.updatePreferences(payload),
}));

import { useSidebarPreferences } from "@/features/settings/useSidebarPreferences";

function serverPreferences(favorites: string[] = [], version = 1): UserPreferences {
  return {
    theme: "system",
    density: "auto",
    contrast: "standard",
    reduce_motion: false,
    accent: "teal",
    workspace: {
      desktop_mode: "auto",
      hidden_routes: [],
      favorite_routes: favorites,
      route_order: [],
      start_route: "/my-work",
    },
    version,
    updated_at: "2026-10-08T00:00:00Z",
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((complete) => {
    resolve = complete;
  });
  return { promise, resolve };
}

function setup() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  function wrapper({ children }: PropsWithChildren) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  }
  return { client, wrapper };
}

function renderHook(
  hook: typeof useSidebarPreferences,
  options: { wrapper: ComponentType<PropsWithChildren> },
) {
  let current: ReturnType<typeof useSidebarPreferences> | undefined;
  function Probe() {
    current = hook();
    return <>{current.persistence}</>;
  }
  const view = render(<Probe />, options);
  return {
    ...view,
    result: {
      get current() {
        if (!current) throw new Error("Sidebar hook has not rendered");
        return current;
      },
    },
    rerender: () => view.rerender(<Probe />),
  };
}

const favorite = (route: string) => ({
  ...defaultSidebarPreferences(),
  favoriteRoutes: [route],
});

describe("account sidebar preferences", () => {
  beforeEach(() => {
    localStorage.clear();
    state.generation = 1;
    state.user = { id: "user-1", active_tenant_id: "tenant-1" };
    state.getPreferences.mockReset().mockResolvedValue(serverPreferences());
    state.updatePreferences.mockReset().mockRejectedValue(new Error("offline"));
  });

  it("restores an unsynchronized favorite over a default server read after reload", async () => {
    const first = setup();
    const initial = renderHook(useSidebarPreferences, { wrapper: first.wrapper });
    await waitFor(() => expect(state.getPreferences).toHaveBeenCalledTimes(1));
    act(() => initial.result.current.save(favorite("/catalog")));
    await waitFor(() => expect(state.updatePreferences).toHaveBeenCalledTimes(1));
    expect(initial.result.current.hasPending).toBe(true);
    initial.unmount();

    const acknowledgment = deferred<UserPreferences>();
    state.updatePreferences.mockReturnValueOnce(acknowledgment.promise);
    const restored = renderHook(useSidebarPreferences, { wrapper: setup().wrapper });
    expect(restored.result.current.preferences.favoriteRoutes).toEqual(["/catalog"]);
    await waitFor(() => expect(state.updatePreferences).toHaveBeenCalledTimes(2));
    expect(restored.result.current.preferences.favoriteRoutes).toEqual(["/catalog"]);
    expect(restored.result.current.hasPending).toBe(true);
    await act(async () => acknowledgment.resolve(serverPreferences(["/catalog"], 2)));
    await waitFor(() => expect(restored.result.current.hasPending).toBe(false));
    expect(restored.result.current.preferences.favoriteRoutes).toEqual(["/catalog"]);
    expect(state.updatePreferences.mock.calls.at(-1)?.[0].workspace.start_route).toBe("/my-work");
  });

  it("keeps the newest local choice while an older save is acknowledged", async () => {
    const firstSave = deferred<UserPreferences>();
    const newestSave = deferred<UserPreferences>();
    state.updatePreferences
      .mockReturnValueOnce(firstSave.promise)
      .mockReturnValueOnce(newestSave.promise);
    const { result } = renderHook(useSidebarPreferences, { wrapper: setup().wrapper });
    await waitFor(() => expect(state.getPreferences).toHaveBeenCalledTimes(1));
    act(() => result.current.save(favorite("/catalog")));
    await waitFor(() => expect(state.updatePreferences).toHaveBeenCalledTimes(1));
    act(() => result.current.save(favorite("/incoming")));
    await act(async () => firstSave.resolve(serverPreferences(["/catalog"], 2)));
    await waitFor(() => expect(state.updatePreferences).toHaveBeenCalledTimes(2));
    expect(result.current.preferences.favoriteRoutes).toEqual(["/incoming"]);
    expect(state.updatePreferences.mock.calls[1]?.[0]).toMatchObject({
      expected_version: 2,
      workspace: { favorite_routes: ["/incoming"], start_route: "/my-work" },
    });
    await act(async () => newestSave.resolve(serverPreferences(["/incoming"], 3)));
    await waitFor(() => expect(result.current.hasPending).toBe(false));
    expect(result.current.preferences.favoriteRoutes).toEqual(["/incoming"]);
  });

  it("does not let a previous account's late acknowledgment change the next account", async () => {
    const oldSave = deferred<UserPreferences>();
    state.updatePreferences.mockReturnValueOnce(oldSave.promise);
    const { result, rerender } = renderHook(useSidebarPreferences, { wrapper: setup().wrapper });
    await waitFor(() => expect(state.getPreferences).toHaveBeenCalledTimes(1));
    act(() => result.current.save(favorite("/catalog")));
    await waitFor(() => expect(state.updatePreferences).toHaveBeenCalledTimes(1));
    state.user = { id: "user-2", active_tenant_id: "tenant-1" };
    state.getPreferences.mockResolvedValue(serverPreferences(["/reports"]));
    rerender();
    await waitFor(() => expect(result.current.preferences.favoriteRoutes).toEqual(["/reports"]));
    await act(async () => oldSave.resolve(serverPreferences(["/catalog"], 2)));
    expect(result.current.scope).toBe("user-2:tenant-1");
    expect(result.current.preferences.favoriteRoutes).toEqual(["/reports"]);
    expect(loadSidebarPreferences("user-2:tenant-1").favoriteRoutes).toEqual(["/reports"]);
    expect(state.updatePreferences).toHaveBeenCalledTimes(1);
  });

  it("isolates restored pending choices by both employee and pharmacy", async () => {
    const queueKey = "aurum:preferences-pending:v1:user-1%3Atenant-1:sidebar-navigation";
    localStorage.setItem(
      queueKey,
      JSON.stringify({ workspace: serverPreferences(["/catalog"]).workspace }),
    );
    const pending = deferred<UserPreferences>();
    state.updatePreferences.mockReturnValueOnce(pending.promise);
    const { result, rerender } = renderHook(useSidebarPreferences, { wrapper: setup().wrapper });
    expect(result.current.preferences.favoriteRoutes).toEqual(["/catalog"]);
    await waitFor(() => expect(state.updatePreferences).toHaveBeenCalledTimes(1));
    state.user = { id: "user-1", active_tenant_id: "tenant-2" };
    rerender();
    expect(result.current.preferences.favoriteRoutes).toEqual([]);
    await waitFor(() => expect(state.getPreferences).toHaveBeenCalledTimes(2));
    expect(result.current.scope).toBe("user-1:tenant-2");
    expect(state.updatePreferences).toHaveBeenCalledTimes(1);
    expect(localStorage.getItem(queueKey)).not.toBeNull();
  });

  it("migrates a customized device cache instead of erasing it with an unsaved server default", async () => {
    saveSidebarPreferences("user-1:tenant-1", favorite("/catalog"));
    const { result } = renderHook(useSidebarPreferences, { wrapper: setup().wrapper });
    await waitFor(() => expect(state.updatePreferences).toHaveBeenCalledTimes(1));
    expect(result.current.preferences.favoriteRoutes).toEqual(["/catalog"]);
    expect(state.updatePreferences.mock.calls[0]?.[0]).toMatchObject({
      workspace: { favorite_routes: ["/catalog"], start_route: "/my-work" },
    });
  });

  it("refreshes an acknowledged device cache from the current server settings", async () => {
    const { client, wrapper } = setup();
    const { result } = renderHook(useSidebarPreferences, { wrapper });
    await waitFor(() => expect(state.getPreferences).toHaveBeenCalledTimes(1));
    act(() =>
      client.setQueryData(
        settingsKeys.preferences("user-1", "tenant-1"),
        serverPreferences(["/reports"], 2),
      ),
    );
    await waitFor(() => expect(result.current.preferences.favoriteRoutes).toEqual(["/reports"]));
    expect(loadSidebarPreferences("user-1:tenant-1").favoriteRoutes).toEqual(["/reports"]);
    expect(state.updatePreferences).not.toHaveBeenCalled();
    expect(result.current.persistence).toBeNull();
  });

  it("does not save anonymous navigation choices into an authenticated queue", () => {
    state.user = null;
    const { result } = renderHook(useSidebarPreferences, { wrapper: setup().wrapper });
    act(() => result.current.save(favorite("/catalog")));
    expect(state.getPreferences).not.toHaveBeenCalled();
    expect(state.updatePreferences).not.toHaveBeenCalled();
    expect(localStorage.length).toBe(0);
  });

  it("rejects a retained save callback after the account session has changed", async () => {
    const { result, rerender } = renderHook(useSidebarPreferences, { wrapper: setup().wrapper });
    await waitFor(() => expect(state.getPreferences).toHaveBeenCalledTimes(1));
    const previousSessionSave = result.current.save;
    state.generation += 1;
    rerender();
    act(() => previousSessionSave(favorite("/catalog")));
    expect(state.updatePreferences).not.toHaveBeenCalled();
    expect(result.current.preferences.favoriteRoutes).toEqual([]);
    expect(loadSidebarPreferences("user-1:tenant-1").favoriteRoutes).toEqual([]);
  });

  it("keeps unsaved choices in memory if browser storage is unavailable", async () => {
    const { result, rerender } = renderHook(useSidebarPreferences, { wrapper: setup().wrapper });
    await waitFor(() => expect(state.getPreferences).toHaveBeenCalledTimes(1));
    const storage = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("Storage full");
    });
    try {
      act(() => result.current.save(favorite("/catalog")));
      await waitFor(() => expect(state.updatePreferences).toHaveBeenCalledTimes(1));
      rerender();
      expect(result.current.hasPending).toBe(true);
      expect(result.current.preferences.favoriteRoutes).toEqual(["/catalog"]);
    } finally {
      storage.mockRestore();
    }
  });

  it("does not activate persistence while hydrating a clean account", async () => {
    state.getPreferences.mockResolvedValue(serverPreferences(["/reports"]));
    const { result } = renderHook(useSidebarPreferences, { wrapper: setup().wrapper });
    expect(result.current.persistence).toBeNull();
    await waitFor(() => expect(result.current.preferences.favoriteRoutes).toEqual(["/reports"]));
    expect(result.current.persistence).toBeNull();
    expect(result.current.hasPending).toBe(false);
    expect(state.updatePreferences).not.toHaveBeenCalled();
  });

  it("does not carry a confirmed version floor into another pharmacy in the same session", async () => {
    state.getPreferences.mockResolvedValue(serverPreferences(["/reports"], 9));
    state.updatePreferences.mockResolvedValueOnce(serverPreferences(["/catalog"], 10));
    const { result, rerender } = renderHook(useSidebarPreferences, { wrapper: setup().wrapper });
    await waitFor(() => expect(result.current.preferences.favoriteRoutes).toEqual(["/reports"]));
    act(() => result.current.save(favorite("/catalog")));
    await waitFor(() => expect(state.updatePreferences).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(result.current.hasPending).toBe(false));
    expect(result.current.preferences.favoriteRoutes).toEqual(["/catalog"]);

    state.user = { id: "user-1", active_tenant_id: "tenant-2" };
    state.getPreferences.mockResolvedValue(serverPreferences(["/incoming"], 1));
    rerender();
    await waitFor(() => expect(result.current.preferences.favoriteRoutes).toEqual(["/incoming"]));
    expect(result.current.scope).toBe("user-1:tenant-2");
    expect(result.current.hasPending).toBe(false);
    expect(result.current.persistence).toBeNull();
    expect(state.updatePreferences).toHaveBeenCalledTimes(1);
  });

  it.each([false, true])(
    "settles a memory-only edit only after server acknowledgment (older cache: %s)",
    async (olderCache) => {
      const acknowledgment = deferred<UserPreferences>();
      if (olderCache) {
        saveSidebarPreferences("user-1:tenant-1", favorite("/reports"));
        localStorage.setItem("aurum:sidebar-server:v1:user-1%3Atenant-1", "1");
        state.getPreferences.mockResolvedValue(serverPreferences(["/reports"]));
      }
      state.updatePreferences.mockReturnValueOnce(acknowledgment.promise);
      const { client, wrapper } = setup();
      const { result } = renderHook(useSidebarPreferences, { wrapper });
      await waitFor(() => expect(state.getPreferences).toHaveBeenCalledTimes(1));
      const storage = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
        throw new Error("Storage full");
      });
      try {
        act(() => result.current.save(favorite("/catalog")));
        expect(result.current.preferences.favoriteRoutes).toEqual(["/catalog"]);
        expect(result.current.hasPending).toBe(true);
        await waitFor(() => expect(state.updatePreferences).toHaveBeenCalledTimes(1));
        expect(result.current.hasPending).toBe(true);
        expect(result.current.preferences.favoriteRoutes).toEqual(["/catalog"]);
        await act(async () => acknowledgment.resolve(serverPreferences(["/catalog"], 2)));
        await waitFor(() => expect(result.current.hasPending).toBe(false));
        expect(result.current.preferences.favoriteRoutes).toEqual(["/catalog"]);
        act(() =>
          client.setQueryData(
            settingsKeys.preferences("user-1", "tenant-1"),
            serverPreferences(["/reports"], 3),
          ),
        );
        await waitFor(() =>
          expect(result.current.preferences.favoriteRoutes).toEqual(["/reports"]),
        );
      } finally {
        storage.mockRestore();
      }
    },
  );
});
