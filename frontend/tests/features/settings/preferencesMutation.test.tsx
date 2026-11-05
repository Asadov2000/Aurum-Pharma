import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { type ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { type UserPreferences } from "@/features/settings/types";

const state = vi.hoisted(() => ({
  user: { id: "user-1", active_tenant_id: "tenant-1" },
  generation: 1,
  update: vi.fn(),
}));
vi.mock("@/features/auth/hooks", () => ({ useAuth: () => ({ user: state.user }) }));
vi.mock("@/stores/auth", () => ({
  getAuthSessionGeneration: () => state.generation,
  useAuthStore: { getState: () => ({ user: state.user }) },
}));
vi.mock("@/features/settings/api", () => ({
  updateUserPreferences: state.update,
  getUserPreferences: vi.fn(),
}));

import { settingsKeys } from "@/features/settings/queries";
import { useUpdateUserPreferences } from "@/features/settings/preferencesMutation";

function preferences(version = 1, startRoute = "/"): UserPreferences {
  return {
    theme: "system",
    density: "auto",
    contrast: "standard",
    reduce_motion: false,
    accent: "teal",
    version,
    updated_at: "2026-10-08T00:00:00Z",
    workspace: {
      desktop_mode: "auto",
      hidden_routes: [],
      favorite_routes: [],
      route_order: [],
      start_route: startRoute,
    },
  };
}

function setup() {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  client.setQueryData(settingsKeys.preferences("user-1", "tenant-1"), preferences());
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return { client, ...renderHook(() => useUpdateUserPreferences(), { wrapper }) };
}

describe("account preference mutation ownership", () => {
  beforeEach(() => {
    state.user = { id: "user-1", active_tenant_id: "tenant-1" };
    state.generation = 1;
    state.update.mockReset();
  });

  it("preserves the latest account start page when a sidebar write leaves the queue", async () => {
    let finish!: (value: UserPreferences) => void;
    state.update.mockReturnValueOnce(
      new Promise<UserPreferences>((resolve) => {
        finish = resolve;
      }),
    );
    state.update.mockResolvedValueOnce(preferences(3, "/my-work"));
    const { result } = setup();
    let first!: Promise<UserPreferences>;
    let second!: Promise<UserPreferences>;
    act(() => {
      first = result.current.mutateAsync({ expected_version: 1, theme: "dark" });
      second = result.current.mutateAsync(
        {
          expected_version: 1,
          workspace: { ...preferences().workspace, favorite_routes: ["/catalog"] },
        },
        { preserveWorkspaceStartRoute: true },
      );
    });
    await waitFor(() => expect(state.update).toHaveBeenCalledTimes(1));
    await act(async () => {
      finish(preferences(2, "/my-work"));
      await Promise.all([first, second]);
    });
    expect(state.update).toHaveBeenLastCalledWith({
      expected_version: 2,
      workspace: {
        ...preferences().workspace,
        favorite_routes: ["/catalog"],
        start_route: "/my-work",
      },
    });
  });

  it("rejects an old account queued write and ignores its late response", async () => {
    let finish!: (value: UserPreferences) => void;
    state.update.mockReturnValueOnce(
      new Promise<UserPreferences>((resolve) => {
        finish = resolve;
      }),
    );
    const { client, result, rerender } = setup();
    let completed!: Promise<PromiseSettledResult<UserPreferences>[]>;
    act(() => {
      completed = Promise.allSettled([
        result.current.mutateAsync({ expected_version: 1, theme: "dark" }),
        result.current.mutateAsync({ expected_version: 1, density: "touch" }),
      ]);
    });
    await waitFor(() => expect(state.update).toHaveBeenCalledTimes(1));
    state.user = { id: "user-2", active_tenant_id: "tenant-2" };
    state.generation += 1;
    rerender();
    let outcomes!: PromiseSettledResult<UserPreferences>[];
    await act(async () => {
      finish(preferences(2));
      outcomes = await completed;
    });
    expect(outcomes[1].status).toBe("rejected");
    expect(state.update).toHaveBeenCalledTimes(1);
    expect(client.getQueryData(settingsKeys.preferences("user-1", "tenant-1"))).toEqual(
      preferences(),
    );
    expect(client.getQueryData(settingsKeys.preferences("user-2", "tenant-2"))).toBeUndefined();
  });
});
