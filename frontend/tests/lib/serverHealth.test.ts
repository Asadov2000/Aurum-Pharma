import { describe, expect, it, vi } from "vitest";

import { checkServerHealth, resolveServerHealthUrl } from "@/lib/serverHealth";

describe("resolveServerHealthUrl", () => {
  it("points absolute API URLs to the backend health endpoint", () => {
    expect(resolveServerHealthUrl("http://localhost:8000/api/v1")).toBe(
      "http://localhost:8000/healthz",
    );
  });

  it("keeps same-origin production URLs relative", () => {
    expect(resolveServerHealthUrl("/api/v1", "https://app.aurum-pharma.tj")).toBe("/healthz");
  });

  it("drops path, query, and hash from configured API URLs", () => {
    expect(resolveServerHealthUrl("https://api.aurum-pharma.tj/proxy/api/v1?x=1#top")).toBe(
      "https://api.aurum-pharma.tj/healthz",
    );
  });
});

describe("checkServerHealth", () => {
  it("returns true when /healthz reports ok", async () => {
    const fetcher = createFetch(
      new Response(JSON.stringify({ status: "ok", db: true, redis: true }), {
        status: 200,
      }),
    );

    await expect(
      checkServerHealth({ fetcher, healthUrl: "/healthz", timeoutMs: 1_000 }),
    ).resolves.toBe(true);
    expect(fetcher).toHaveBeenCalledWith(
      "/healthz",
      expect.objectContaining({
        cache: "no-store",
        method: "GET",
      }),
    );
  });

  it("returns false when /healthz reports degraded", async () => {
    const fetcher = createFetch(
      new Response(JSON.stringify({ status: "degraded", db: true, redis: false }), {
        status: 200,
      }),
    );

    await expect(
      checkServerHealth({ fetcher, healthUrl: "/healthz", timeoutMs: 1_000 }),
    ).resolves.toBe(false);
  });

  it("returns false when the request fails", async () => {
    const fetcher = vi.fn(async () => {
      throw new TypeError("network failed");
    }) as unknown as typeof fetch;

    await expect(
      checkServerHealth({ fetcher, healthUrl: "/healthz", timeoutMs: 1_000 }),
    ).resolves.toBe(false);
  });

  it("returns false for malformed health payloads", async () => {
    const fetcher = createFetch(new Response("{", { status: 200 }));

    await expect(
      checkServerHealth({ fetcher, healthUrl: "/healthz", timeoutMs: 1_000 }),
    ).resolves.toBe(false);
  });

  it("passes an already aborted caller signal to the request", async () => {
    const caller = new AbortController();
    caller.abort();
    const fetcher = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      expect(init?.signal?.aborted).toBe(true);
      throw new DOMException("Aborted", "AbortError");
    }) as unknown as typeof fetch;

    await expect(
      checkServerHealth({
        fetcher,
        healthUrl: "/healthz",
        signal: caller.signal,
        timeoutMs: 1_000,
      }),
    ).resolves.toBe(false);
  });
});

function createFetch(response: Response): typeof fetch {
  return vi.fn(async () => response) as unknown as typeof fetch;
}
