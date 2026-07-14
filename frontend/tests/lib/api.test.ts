import { afterEach, describe, expect, it, vi } from "vitest";

import {
  configureAuthHooks,
  refreshAccessToken,
  resolveApiBaseUrl,
} from "@/lib/api";

afterEach(() => {
  configureAuthHooks({
    getAccessToken: () => null,
    refreshTokens: async () => null,
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
    expect(resolveApiBaseUrl({ DEV: false, VITE_API_URL: undefined })).toBe(
      "/api/v1",
    );
  });
});

describe("refreshAccessToken", () => {
  it("shares one refresh between concurrent requests", async () => {
    const refreshTokens = vi.fn(async () => "access");
    configureAuthHooks({
      getAccessToken: () => null,
      refreshTokens,
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
      onAuthFailure,
    });

    await expect(refreshAccessToken()).rejects.toThrow("offline");
    expect(onAuthFailure).not.toHaveBeenCalled();
  });
});
