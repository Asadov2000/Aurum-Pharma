import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { type MeResponse } from "@/features/auth/types";
import { type SupportAccessSession } from "@/features/supportAccess/types";
import { useSupportAccessStore } from "@/stores/supportAccess";

const mocks = vi.hoisted(() => ({
  clearContext: vi.fn(),
  navigate: vi.fn(),
  revoke: vi.fn(),
  user: null as MeResponse | null,
}));

vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => mocks.navigate,
}));

vi.mock("@/features/auth/hooks", () => ({
  useAuth: () => ({ user: mocks.user }),
}));

vi.mock("@/features/supportAccess/context", () => ({
  clearSupportContext: () => mocks.clearContext(),
}));

vi.mock("@/features/supportAccess/queries", () => ({
  useRevokeSupportSession: () => ({
    isPending: false,
    mutateAsync: mocks.revoke,
  }),
}));

import { SupportAccessBanner } from "@/features/supportAccess/SupportAccessBanner";

function session(expiresAt: string): SupportAccessSession {
  return {
    id: "11111111-1111-4111-8111-111111111111",
    tenant_id: "22222222-2222-4222-8222-222222222222",
    tenant_name: "Shifo",
    actor_user_id: "33333333-3333-4333-8333-333333333333",
    reason: "Role setup before launch",
    capabilities: ["users.view"],
    is_read_only: true,
    started_at: "2026-07-22T10:00:00Z",
    expires_at: expiresAt,
    revoked_at: null,
  };
}

describe("SupportAccessBanner", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-22T10:00:00Z"));
    mocks.clearContext.mockReset().mockImplementation(async () => {
      useSupportAccessStore.getState().clear();
    });
    mocks.navigate.mockReset().mockResolvedValue(undefined);
    mocks.revoke.mockReset().mockResolvedValue(undefined);
    useSupportAccessStore.getState().clear();
  });

  afterEach(() => {
    useSupportAccessStore.getState().clear();
    mocks.user = null;
    vi.useRealTimers();
  });

  it("drops the local tenant context when the short session expires", async () => {
    const active = session("2026-07-22T10:00:01Z");
    useSupportAccessStore.getState().setActive(active);
    mocks.user = {
      support_access: active,
    } as MeResponse;

    render(<SupportAccessBanner />);
    expect(screen.getByText("Shifo")).toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });

    expect(mocks.clearContext).toHaveBeenCalledTimes(1);
    expect(useSupportAccessStore.getState().active).toBeNull();
    expect(mocks.navigate).toHaveBeenCalledWith({ to: "/admin/tenants" });
    expect(mocks.revoke).not.toHaveBeenCalled();
  });

  it("leaves the tenant context locally when server revocation is offline", async () => {
    const active = session("2026-07-22T10:15:00Z");
    useSupportAccessStore.getState().setActive(active);
    mocks.user = {
      support_access: active,
    } as MeResponse;
    mocks.revoke.mockRejectedValueOnce(new Error("offline"));

    render(<SupportAccessBanner />);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Завершить" }));
      await Promise.resolve();
    });

    expect(mocks.clearContext).toHaveBeenCalledTimes(1);
    expect(useSupportAccessStore.getState().active).toBeNull();
    expect(mocks.navigate).toHaveBeenCalledWith({ to: "/admin/tenants" });
  });
});
