import { describe, expect, it } from "vitest";

import { resolveApiBaseUrl } from "@/lib/api";

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
