import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { defaultQuickActionPreferences } from "@/features/settings/quickActions";
import {
  loadQuickActionPreferences,
  saveQuickActionPreferences,
} from "@/features/settings/quickActionStorage";
import { type UserPreferences } from "@/features/settings/types";

const state = vi.hoisted(() => ({
  user: { id: "user-1", active_tenant_id: "tenant-1" },
  loaded: true,
  preferences: undefined as UserPreferences | undefined,
  mutateAsync: vi.fn(),
  refetch: vi.fn(),
}));

vi.mock("@/features/auth/hooks", () => ({ useAuth: () => ({ user: state.user }) }));
vi.mock("@/features/settings/queries", () => ({
  useUserPreferencesQuery: () => ({
    data: state.loaded ? state.preferences : undefined,
    refetch: state.refetch,
  }),
  useUpdateUserPreferences: () => ({
    mutateAsync: state.mutateAsync,
    isPending: false,
    error: null,
  }),
}));

import { useQuickActionPreferences } from "@/features/settings/useQuickActionPreferences";

function serverPreferences(): UserPreferences {
  return {
    theme: "system",
    density: "auto",
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
    quick_actions: {
      catalog: defaultQuickActionPreferences("catalog"),
      batches: defaultQuickActionPreferences("batches"),
    },
    version: 1,
    updated_at: "2026-10-03T00:00:00Z",
  };
}

describe("useQuickActionPreferences", () => {
  beforeEach(() => {
    localStorage.clear();
    state.user = { id: "user-1", active_tenant_id: "tenant-1" };
    state.loaded = true;
    state.preferences = serverPreferences();
    state.mutateAsync.mockReset();
    state.refetch.mockReset();
    state.refetch.mockImplementation(async () => ({ data: state.preferences }));
  });

  it("keeps saved local choices over a stale GET until reconnect synchronizes the queued section", async () => {
    state.mutateAsync.mockRejectedValueOnce(new Error("offline"));
    const { result, rerender, unmount } = renderHook(() => useQuickActionPreferences("catalog"));
    const changed = { enabled: false, order: ["copy_name", "open"], hidden: ["edit"] };
    act(() => result.current.save(changed));
    await waitFor(() => expect(state.mutateAsync).toHaveBeenCalledTimes(1));
    state.preferences = { ...serverPreferences(), version: 2 };
    rerender();
    expect(result.current.config.enabled).toBe(false);
    expect(result.current.config.hidden).toEqual(["edit"]);
    unmount();

    state.loaded = false;
    const restored = renderHook(() => useQuickActionPreferences("catalog"));
    expect(restored.result.current.config.enabled).toBe(false);
    expect(restored.result.current.hasPending).toBe(true);
    state.mutateAsync.mockImplementation(
      async (payload: { quick_actions: UserPreferences["quick_actions"] }) => {
        state.preferences = {
          ...serverPreferences(),
          quick_actions: { ...serverPreferences().quick_actions!, ...payload.quick_actions },
          version: 3,
        };
        return state.preferences;
      },
    );
    state.loaded = true;
    act(() => window.dispatchEvent(new Event("online")));
    await waitFor(() => expect(restored.result.current.hasPending).toBe(false));
    expect(state.mutateAsync.mock.calls.at(-1)?.[0]).toMatchObject({
      expected_version: 2,
      quick_actions: { catalog: { enabled: false, hidden: ["edit"] } },
    });
    expect(Object.keys(state.mutateAsync.mock.calls.at(-1)?.[0].quick_actions)).toEqual([
      "catalog",
    ]);
    expect(restored.result.current.config.enabled).toBe(false);
    expect(loadQuickActionPreferences("user-1:tenant-1", "batches")).toBeNull();
  });

  it("uses the new user and tenant cache immediately without leaking the previous choices or queue", () => {
    state.loaded = false;
    saveQuickActionPreferences("user-1:tenant-1", "catalog", {
      enabled: false,
      order: ["copy_name"],
      hidden: ["open"],
    });
    saveQuickActionPreferences("user-2:tenant-1", "catalog", {
      enabled: true,
      order: ["copy_barcode"],
      hidden: ["edit"],
    });
    const { result, rerender } = renderHook(() => useQuickActionPreferences("catalog"));
    act(() => result.current.save({ enabled: false, order: ["copy_name"], hidden: ["open"] }));
    state.user = { id: "user-2", active_tenant_id: "tenant-1" };
    rerender();
    expect(result.current.scope).toBe("user-2:tenant-1");
    expect(result.current.config.order[0]).toBe("copy_barcode");
    expect(result.current.config.hidden).toEqual(["edit"]);
    expect(result.current.hasPending).toBe(false);
    state.user = { id: "user-2", active_tenant_id: "tenant-2" };
    rerender();
    expect(result.current.config.hidden).toEqual([]);
    expect(result.current.config.enabled).toBe(true);
    expect(state.mutateAsync).not.toHaveBeenCalled();
  });

  it("retains saved choices in memory while storage is unavailable and releases them after server confirmation", async () => {
    const storageWrite = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("Storage unavailable");
    });
    try {
      state.mutateAsync.mockRejectedValueOnce(new Error("offline"));
      const { result, rerender } = renderHook(() => useQuickActionPreferences("catalog"));
      act(() => result.current.save({ enabled: false, order: ["copy_name"], hidden: ["edit"] }));
      await waitFor(() => expect(state.mutateAsync).toHaveBeenCalledTimes(1));
      expect(result.current.hasPending).toBe(true);
      expect(result.current.config).toMatchObject({ enabled: false, hidden: ["edit"] });
      state.preferences = { ...serverPreferences(), version: 2 };
      rerender();
      expect(result.current.config.order[0]).toBe("copy_name");
      expect(result.current.config.enabled).toBe(false);

      state.mutateAsync.mockImplementation(
        async (payload: { quick_actions: UserPreferences["quick_actions"] }) => {
          state.preferences = {
            ...serverPreferences(),
            quick_actions: { ...serverPreferences().quick_actions!, ...payload.quick_actions },
            version: 3,
          };
          return state.preferences;
        },
      );
      act(() => window.dispatchEvent(new Event("online")));
      await waitFor(() => expect(result.current.hasPending).toBe(false));
      expect(result.current.config.enabled).toBe(false);
      state.preferences = serverPreferences();
      rerender();
      expect(result.current.config.enabled).toBe(true);
      expect(result.current.config.hidden).toEqual([]);
    } finally {
      storageWrite.mockRestore();
    }
  });

  it("prefers the latest in-memory edit to an older queue entry when storage fills up", () => {
    state.loaded = false;
    localStorage.setItem(
      "aurum:preferences-pending:v1:user-1%3Atenant-1:quick-actions%3Acatalog",
      JSON.stringify({ quick_actions: { catalog: defaultQuickActionPreferences("catalog") } }),
    );
    const { result, rerender } = renderHook(() => useQuickActionPreferences("catalog"));
    const storageWrite = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("Storage full");
    });
    try {
      act(() => result.current.save({ enabled: false, order: ["copy_name"], hidden: ["edit"] }));
      expect(result.current.config.enabled).toBe(false);
      expect(result.current.config.hidden).toEqual(["edit"]);
      state.user = { id: "user-2", active_tenant_id: "tenant-2" };
      rerender();
      expect(result.current.config.enabled).toBe(true);
      expect(result.current.config.hidden).toEqual([]);
      expect(result.current.hasPending).toBe(false);
    } finally {
      storageWrite.mockRestore();
    }
  });

  it("refreshes a clean local cache from successful server reads", () => {
    saveQuickActionPreferences("user-1:tenant-1", "catalog", {
      enabled: false,
      order: [],
      hidden: [],
    });
    const { result, rerender } = renderHook(() => useQuickActionPreferences("catalog"));
    expect(result.current.config.enabled).toBe(true);
    state.preferences = {
      ...serverPreferences(),
      quick_actions: {
        catalog: { enabled: false, order: ["copy_name"], hidden: ["edit"] },
        batches: defaultQuickActionPreferences("batches"),
      },
    };
    rerender();
    expect(result.current.config.enabled).toBe(false);
    expect(result.current.config.order[0]).toBe("copy_name");
    act(() =>
      saveQuickActionPreferences(
        "user-1:tenant-1",
        "catalog",
        defaultQuickActionPreferences("catalog"),
      ),
    );
    expect(result.current.config.enabled).toBe(false);
    expect(result.current.config.order[0]).toBe("copy_name");
  });

  it("ignores an all-hidden cache and queue instead of sending an invalid request", () => {
    state.loaded = false;
    const defaults = defaultQuickActionPreferences("catalog");
    const invalid = { ...defaults, enabled: false, hidden: defaults.order };
    localStorage.setItem(
      "aurum:quick-actions:v1:user-1%3Atenant-1:catalog",
      JSON.stringify(invalid),
    );
    localStorage.setItem(
      "aurum:preferences-pending:v1:user-1%3Atenant-1:quick-actions%3Acatalog",
      JSON.stringify({ quick_actions: { catalog: invalid } }),
    );
    const { result, rerender } = renderHook(() => useQuickActionPreferences("catalog"));
    expect(result.current.config.hidden).toEqual([]);
    expect(result.current.config.enabled).toBe(true);
    expect(result.current.hasPending).toBe(false);
    state.loaded = true;
    rerender();
    expect(state.mutateAsync).not.toHaveBeenCalled();
  });

  it("normalizes old queued IDs to a unique known complete order accepted by the server", async () => {
    state.mutateAsync.mockRejectedValueOnce(new Error("offline"));
    localStorage.setItem(
      "aurum:preferences-pending:v1:user-1%3Atenant-1:quick-actions%3Abatches",
      JSON.stringify({
        quick_actions: {
          batches: {
            enabled: false,
            order: ["copy_number", "removed", "copy_number"],
            hidden: ["removed", "open", "open"],
          },
        },
      }),
    );
    const { result } = renderHook(() => useQuickActionPreferences("batches"));
    await waitFor(() =>
      expect(state.mutateAsync).toHaveBeenCalledExactlyOnceWith({
        expected_version: 1,
        quick_actions: {
          batches: { enabled: false, order: ["copy_number", "open"], hidden: ["open"] },
        },
      }),
    );
    expect(result.current.config).toEqual({
      enabled: false,
      order: ["copy_number", "open"],
      hidden: ["open"],
    });
  });

  it("rejects unsafe cache values and malformed queued preferences", () => {
    state.loaded = false;
    localStorage.setItem(
      "aurum:quick-actions:v1:user-1%3Atenant-1:catalog",
      JSON.stringify({ enabled: false, order: [], hidden: [], patient: "forbidden" }),
    );
    localStorage.setItem(
      "aurum:preferences-pending:v1:user-1%3Atenant-1:quick-actions%3Acatalog",
      JSON.stringify({ quick_actions: { catalog: { enabled: "false", order: [], hidden: [] } } }),
    );
    const { result } = renderHook(() => useQuickActionPreferences("catalog"));
    expect(result.current.config.enabled).toBe(true);
    expect(result.current.hasPending).toBe(false);
    expect(state.mutateAsync).not.toHaveBeenCalled();
  });
});
