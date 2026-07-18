import { AxiosError, type AxiosResponse, type InternalAxiosRequestConfig } from "axios";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  api,
  configureAuthHooks,
  isMfaStepUpRequired,
  refreshAccessToken,
  requestStepUpAccessToken,
  resolveApiBaseUrl,
} from "@/lib/api";

const defaultAdapter = api.defaults.adapter;

afterEach(() => {
  api.defaults.adapter = defaultAdapter;
  configureAuthHooks({
    getAccessToken: () => null,
    refreshTokens: async () => null,
    requestMfaStepUp: async () => null,
    onAuthFailure: () => {},
  });
});

describe("resolveApiBaseUrl", () => {
  it("uses an explicit VITE_API_URL when configured", () => {
    expect(
      resolveApiBaseUrl({
        DEV: false,
        VITE_API_URL: "https://api.aurum-pharma.tj/api/v1",
      }),
    ).toBe("https://api.aurum-pharma.tj/api/v1");
  });

  it("trims accidental whitespace around VITE_API_URL", () => {
    expect(
      resolveApiBaseUrl({
        DEV: false,
        VITE_API_URL: "  https://api.aurum-pharma.tj/api/v1  ",
      }),
    ).toBe("https://api.aurum-pharma.tj/api/v1");
  });

  it("keeps the local backend fallback in development", () => {
    expect(resolveApiBaseUrl({ DEV: true, VITE_API_URL: undefined })).toBe(
      "http://localhost:8000/api/v1",
    );
  });

  it("uses same-origin API fallback in production", () => {
    expect(resolveApiBaseUrl({ DEV: false, VITE_API_URL: undefined })).toBe("/api/v1");
  });
});

describe("refreshAccessToken", () => {
  it("shares one refresh between concurrent requests", async () => {
    const refreshTokens = vi.fn(async () => "access");
    configureAuthHooks({
      getAccessToken: () => null,
      refreshTokens,
      requestMfaStepUp: async () => null,
      onAuthFailure: () => {},
    });

    const results = await Promise.all([refreshAccessToken(), refreshAccessToken()]);

    expect(results).toEqual(["access", "access"]);
    expect(refreshTokens).toHaveBeenCalledTimes(1);
  });

  it("reports a confirmed auth rejection once", async () => {
    const onAuthFailure = vi.fn();
    configureAuthHooks({
      getAccessToken: () => null,
      refreshTokens: async () => null,
      requestMfaStepUp: async () => null,
      onAuthFailure,
    });

    await Promise.all([refreshAccessToken(), refreshAccessToken()]);

    expect(onAuthFailure).toHaveBeenCalledTimes(1);
  });

  it("does not report a transient refresh error as logout", async () => {
    const onAuthFailure = vi.fn();
    configureAuthHooks({
      getAccessToken: () => null,
      refreshTokens: async () => {
        throw new Error("offline");
      },
      requestMfaStepUp: async () => null,
      onAuthFailure,
    });

    await expect(refreshAccessToken()).rejects.toThrow("offline");
    expect(onAuthFailure).not.toHaveBeenCalled();
  });
});

describe("MFA step-up interceptor", () => {
  it("recognizes only the structured step-up response", () => {
    const response = {
      status: 403,
      data: {
        error: {
          details: { reason: "mfa_step_up_required" },
        },
      },
    } as AxiosResponse;
    const error = new AxiosError(
      "Request failed",
      "ERR_BAD_RESPONSE",
      undefined,
      undefined,
      response,
    );

    expect(isMfaStepUpRequired(error)).toBe(true);
    response.data.error.details.reason = "other_reason";
    expect(isMfaStepUpRequired(error)).toBe(false);
  });

  it("shares one prompt between concurrent callers", async () => {
    let resolvePrompt: (token: string | null) => void = () => {};
    const requestMfaStepUp = vi.fn(
      () =>
        new Promise<string | null>((resolve) => {
          resolvePrompt = resolve;
        }),
    );
    configureAuthHooks({
      getAccessToken: () => "old-access",
      refreshTokens: async () => null,
      requestMfaStepUp,
      onAuthFailure: () => {},
    });

    const first = requestStepUpAccessToken();
    const second = requestStepUpAccessToken();
    resolvePrompt("new-access");

    await expect(Promise.all([first, second])).resolves.toEqual(["new-access", "new-access"]);
    expect(requestMfaStepUp).toHaveBeenCalledTimes(1);
  });

  it("retries the protected request once with the new token", async () => {
    let accessToken = "old-access";
    const authorizationHeaders: string[] = [];
    let attempts = 0;
    api.defaults.adapter = vi.fn(async (config: InternalAxiosRequestConfig) => {
      attempts += 1;
      authorizationHeaders.push(String(config.headers.get("Authorization")));
      if (attempts === 1) {
        const response: AxiosResponse = {
          data: {
            error: {
              code: "permission_denied",
              details: { reason: "mfa_step_up_required" },
            },
          },
          status: 403,
          statusText: "Forbidden",
          headers: {},
          config,
        };
        throw new AxiosError("Request failed", "ERR_BAD_RESPONSE", config, undefined, response);
      }
      return {
        data: { status: "ok" },
        status: 200,
        statusText: "OK",
        headers: {},
        config,
      };
    });
    const requestMfaStepUp = vi.fn(async () => {
      accessToken = "new-access";
      return accessToken;
    });
    configureAuthHooks({
      getAccessToken: () => accessToken,
      refreshTokens: async () => null,
      requestMfaStepUp,
      onAuthFailure: () => {},
    });

    const response = await api.post("/protected-action", { value: 1 });

    expect(response.data).toEqual({ status: "ok" });
    expect(requestMfaStepUp).toHaveBeenCalledTimes(1);
    expect(authorizationHeaders).toEqual(["Bearer old-access", "Bearer new-access"]);
  });
});
