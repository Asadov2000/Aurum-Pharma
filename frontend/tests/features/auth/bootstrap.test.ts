import { AxiosError, type AxiosResponse } from "axios";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const authApi = vi.hoisted(() => ({
  refreshTokensRequest: vi.fn(),
  restorationBlocked: false,
}));

vi.mock("@/features/auth/api", () => authApi);
vi.mock("@/features/auth/logoutState", () => ({
  isSessionRestorationBlocked: () => authApi.restorationBlocked,
  getAuthEpoch: () => null,
}));

import { refreshSessionFromCookie } from "@/features/auth/bootstrap";
import { getPendingRefreshOperationId } from "@/features/auth/refreshOperation";
import { useAuthStore } from "@/stores/auth";

function responseError(status: number): AxiosError {
  const response = { status } as AxiosResponse;
  return new AxiosError("Request failed", "ERR_BAD_RESPONSE", undefined, undefined, response);
}

describe("auth bootstrap refresh", () => {
  beforeEach(() => {
    authApi.restorationBlocked = false;
    authApi.refreshTokensRequest.mockReset();
    useAuthStore.getState().clear();
    window.sessionStorage.clear();
  });

  afterEach(() => {
    authApi.restorationBlocked = false;
    useAuthStore.getState().clear();
    window.sessionStorage.clear();
  });

  it("does not restore a cookie session behind a persistent logout barrier", async () => {
    authApi.restorationBlocked = true;
    await expect(refreshSessionFromCookie([0])).resolves.toBeNull();
    expect(authApi.refreshTokensRequest).not.toHaveBeenCalled();
    expect(getPendingRefreshOperationId()).toBeNull();
  });

  it("rejects bootstrap completed after logout even without an initial access token", async () => {
    let finish!: (value: unknown) => void;
    authApi.refreshTokensRequest.mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const pending = refreshSessionFromCookie([0]);
    const rejected = expect(pending).rejects.toMatchObject({ code: "ERR_CANCELED" });
    useAuthStore.getState().clear();
    finish({ access_token: "stale-bootstrap", token_type: "bearer", expires_in: 900 });
    await rejected;
    expect(useAuthStore.getState().accessToken).toBeNull();
  });

  it("uses the same operation id after a network failure", async () => {
    authApi.refreshTokensRequest
      .mockRejectedValueOnce(new AxiosError("Network Error", "ERR_NETWORK"))
      .mockResolvedValueOnce({
        access_token: "new-access",
        token_type: "bearer",
        expires_in: 900,
      });

    await expect(refreshSessionFromCookie([0, 0])).resolves.toBe("new-access");

    expect(authApi.refreshTokensRequest).toHaveBeenCalledTimes(2);
    expect(authApi.refreshTokensRequest.mock.calls[0]?.[0]).toBe(
      authApi.refreshTokensRequest.mock.calls[1]?.[0],
    );
    expect(getPendingRefreshOperationId()).toBeNull();
  });

  it("retries a temporary server failure without changing the operation", async () => {
    authApi.refreshTokensRequest.mockRejectedValueOnce(responseError(503)).mockResolvedValueOnce({
      access_token: "recovered-access",
      token_type: "bearer",
      expires_in: 900,
    });

    await refreshSessionFromCookie([0, 0]);

    expect(authApi.refreshTokensRequest.mock.calls[0]?.[0]).toBe(
      authApi.refreshTokensRequest.mock.calls[1]?.[0],
    );
  });

  it("keeps the current auth state when temporary failures are exhausted", async () => {
    useAuthStore.getState().setTokens({
      access_token: "current-access",
      token_type: "bearer",
      expires_in: 900,
    });
    authApi.refreshTokensRequest.mockRejectedValue(new AxiosError("Network Error", "ERR_NETWORK"));

    await expect(refreshSessionFromCookie([0])).rejects.toThrow("Network Error");

    expect(useAuthStore.getState().accessToken).toBe("current-access");
    expect(getPendingRefreshOperationId()).not.toBeNull();
  });

  it("returns a confirmed auth rejection without retrying", async () => {
    authApi.refreshTokensRequest.mockRejectedValue(responseError(401));

    await expect(refreshSessionFromCookie([0, 0])).resolves.toBeNull();
    expect(authApi.refreshTokensRequest).toHaveBeenCalledTimes(1);
  });

  it.each(["success", "network failure", "auth failure"])(
    "does not update the new session or retry a stale refresh %s",
    async (outcome) => {
      useAuthStore
        .getState()
        .setTokens({ access_token: "old-access", token_type: "bearer", expires_in: 900 });
      let resolveRefresh!: (value: unknown) => void;
      let rejectRefresh!: (error: unknown) => void;
      authApi.refreshTokensRequest.mockImplementation(
        () =>
          new Promise((resolve, reject) => {
            resolveRefresh = resolve;
            rejectRefresh = reject;
          }),
      );
      const rejection = expect(refreshSessionFromCookie([0, 0])).rejects.toMatchObject({
        code: "ERR_CANCELED",
      });
      useAuthStore.getState().clear();
      useAuthStore
        .getState()
        .setTokens({ access_token: "new-login-access", token_type: "bearer", expires_in: 900 });
      if (outcome === "success") {
        resolveRefresh({
          access_token: "stale-refresh-access",
          token_type: "bearer",
          expires_in: 900,
        });
      } else {
        rejectRefresh(
          outcome === "auth failure"
            ? responseError(401)
            : new AxiosError("offline", "ERR_NETWORK"),
        );
      }
      await rejection;
      expect(useAuthStore.getState().accessToken).toBe("new-login-access");
      expect(authApi.refreshTokensRequest).toHaveBeenCalledTimes(1);
    },
  );

  it("does not issue a delayed refresh retry after logout", async () => {
    vi.useFakeTimers();
    try {
      useAuthStore
        .getState()
        .setTokens({ access_token: "old-access", token_type: "bearer", expires_in: 900 });
      authApi.refreshTokensRequest.mockRejectedValueOnce(responseError(503));
      const rejection = expect(refreshSessionFromCookie([0, 300])).rejects.toMatchObject({
        code: "ERR_CANCELED",
      });
      await vi.advanceTimersByTimeAsync(0);
      useAuthStore.getState().clear();
      await vi.advanceTimersByTimeAsync(300);
      await rejection;
      expect(authApi.refreshTokensRequest).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });
});
