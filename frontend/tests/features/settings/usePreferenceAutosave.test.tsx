import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { type UserPreferences } from "@/features/settings/types";

interface Deferred<T> {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (reason?: unknown) => void;
}

const state = vi.hoisted(() => ({
  mutateAsync: vi.fn(),
  refetch: vi.fn(),
  preferences: {
    theme: "system",
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
    version: 1,
    updated_at: "2026-08-23T00:00:00Z",
  } as UserPreferences,
}));

vi.mock("@/features/auth/hooks", () => ({
  useAuth: () => ({ user: { id: "user-1", active_tenant_id: "tenant-1" } }),
}));

vi.mock("@/features/settings/queries", () => ({
  useUserPreferencesQuery: () => ({
    data: state.preferences,
    error: null,
    refetch: state.refetch,
  }),
  useUpdateUserPreferences: () => ({
    error: null,
    isPending: false,
    mutateAsync: state.mutateAsync,
  }),
}));

import { usePreferenceAutosave } from "@/features/settings/usePreferenceAutosave";

describe("usePreferenceAutosave", () => {
  beforeEach(() => {
    window.localStorage.clear();
    state.mutateAsync.mockReset();
    state.refetch.mockReset();
    state.preferences = { ...state.preferences, version: 1 };
    state.refetch.mockResolvedValue({ data: state.preferences });
  });

  it("serializes rapid changes and keeps the in-flight patch until the server confirms it", async () => {
    const first = deferred<UserPreferences>();
    const second = deferred<UserPreferences>();
    state.mutateAsync.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    const { result } = renderHook(() => usePreferenceAutosave("test-channel"));

    act(() => result.current.enqueue({ theme: "dark" }));
    await waitFor(() =>
      expect(state.mutateAsync).toHaveBeenNthCalledWith(1, {
        expected_version: 1,
        theme: "dark",
      }),
    );

    act(() => result.current.enqueue({ density: "touch" }));
    const storageKey = "aurum:preferences-pending:v1:user-1%3Atenant-1:test-channel";
    expect(JSON.parse(window.localStorage.getItem(storageKey) ?? "{}")).toMatchObject({
      theme: "dark",
      density: "touch",
    });

    act(() => first.resolve({ ...state.preferences, theme: "dark", version: 2 }));
    await waitFor(() =>
      expect(state.mutateAsync).toHaveBeenNthCalledWith(2, {
        expected_version: 2,
        density: "touch",
      }),
    );
    act(() => second.resolve({ ...state.preferences, density: "touch", version: 3 }));

    await waitFor(() => expect(result.current.hasPending).toBe(false));
    expect(window.localStorage.getItem(storageKey)).toBeNull();
  });
});

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}
