import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import { type ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { captureSupportAuthority } from "@/features/supportAccess/authority";
import { useRevokeSupportSession, useStartSupportSession } from "@/features/supportAccess/queries";
import { useSupportAccessStore } from "@/stores/supportAccess";

const mocks = vi.hoisted(() => ({ start: vi.fn(), revoke: vi.fn() }));
vi.mock("@/features/supportAccess/api", () => ({
  startSupportSession: (...args: unknown[]) => mocks.start(...args),
  revokeSupportSession: (...args: unknown[]) => mocks.revoke(...args),
}));

const payload = {
  tenant_id: "tenant",
  reason: "Настройка ролей",
  duration_minutes: 5,
  capabilities: ["users.view"],
};

function setup() {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return { client, wrapper };
}

afterEach(() => {
  vi.resetAllMocks();
  useSupportAccessStore.getState().clear();
});

describe("queued support mutations", () => {
  it("does not create a session when authority changes before mutation execution", async () => {
    const { client, wrapper } = setup();
    const { result } = renderHook(useStartSupportSession, { wrapper });
    const authority = captureSupportAuthority();
    await act(async () => {
      const pending = result.current.mutateAsync({ payload, authority });
      useSupportAccessStore.getState().clear();
      await expect(pending).rejects.toThrow("Support access session changed");
    });
    expect(mocks.start).not.toHaveBeenCalled();
    client.clear();
  });

  it("does not revoke under a newer context when a mutation is queued", async () => {
    const { client, wrapper } = setup();
    const { result } = renderHook(useRevokeSupportSession, { wrapper });
    const authority = captureSupportAuthority();
    await act(async () => {
      const pending = result.current.mutateAsync({ sessionId: "old", authority });
      useSupportAccessStore.getState().clear();
      await expect(pending).rejects.toThrow("Support access session changed");
    });
    expect(mocks.revoke).not.toHaveBeenCalled();
    client.clear();
  });

  it("allows a current queued activation", async () => {
    mocks.start.mockResolvedValueOnce({ id: "current" });
    const { client, wrapper } = setup();
    const { result } = renderHook(useStartSupportSession, { wrapper });
    await act(async () => {
      await expect(
        result.current.mutateAsync({ payload, authority: captureSupportAuthority() }),
      ).resolves.toEqual({ id: "current" });
    });
    expect(mocks.start).toHaveBeenCalledWith(payload);
    client.clear();
  });
});
