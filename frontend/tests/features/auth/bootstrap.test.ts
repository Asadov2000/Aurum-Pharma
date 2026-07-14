import { AxiosError, type AxiosResponse } from "axios";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const authApi = vi.hoisted(() => ({
  refreshTokensRequest: vi.fn(),
}));

vi.mock("@/features/auth/api", () => authApi);

import { refreshSessionFromCookie } from "@/features/auth/bootstrap";
import { getPendingRefreshOperationId } from "@/features/auth/refreshOperation";
import { useAuthStore } from "@/stores/auth";

function responseError(status: number): AxiosError {
  const response = { status } as AxiosResponse;
  return new AxiosError("Request failed", "ERR_BAD_RESPONSE", undefined, undefined, response);
}

describe("auth bootstrap refresh", () => {
  beforeEach(() => {
    authApi.refreshTokensRequest.mockReset();
    useAuthStore.getState().clear();
    window.sessionStorage.clear();
  });

  afterEach(() => {
    useAuthStore.getState().clear();
    window.sessionStorage.clear();
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
    authApi.refreshTokensRequest
      .mockRejectedValueOnce(responseError(503))
      .mockResolvedValueOnce({
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
    authApi.refreshTokensRequest.mockRejectedValue(
      new AxiosError("Network Error", "ERR_NETWORK"),
    );

    await expect(refreshSessionFromCookie([0])).rejects.toThrow("Network Error");

    expect(useAuthStore.getState().accessToken).toBe("current-access");
    expect(getPendingRefreshOperationId()).not.toBeNull();
  });

  it("returns a confirmed auth rejection without retrying", async () => {
    authApi.refreshTokensRequest.mockRejectedValue(responseError(401));

    await expect(refreshSessionFromCookie([0, 0])).resolves.toBeNull();
    expect(authApi.refreshTokensRequest).toHaveBeenCalledTimes(1);
  });
});
