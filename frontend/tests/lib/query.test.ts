import { describe, expect, it } from "vitest";

import { queryClient, shouldRetryQuery } from "@/lib/query";

function axiosError(status?: number): Error {
  return Object.assign(new Error("request failed"), {
    isAxiosError: true,
    response: status === undefined ? undefined : { status },
  });
}

describe("query defaults", () => {
  it("keeps recently visited sections cached", () => {
    const defaults = queryClient.getDefaultOptions().queries;

    expect(defaults?.staleTime).toBe(30_000);
    expect(defaults?.gcTime).toBe(15 * 60_000);
  });

  it.each([400, 401, 403, 404, 409, 422])(
    "does not retry a non-transient HTTP %s response",
    (status) => {
      expect(shouldRetryQuery(0, axiosError(status))).toBe(false);
    },
  );

  it.each([408, 425, 429, 500, 502, 503, 504])(
    "retries a transient HTTP %s response once",
    (status) => {
      expect(shouldRetryQuery(0, axiosError(status))).toBe(true);
      expect(shouldRetryQuery(1, axiosError(status))).toBe(false);
    },
  );

  it("retries one Axios network failure but not application errors", () => {
    expect(shouldRetryQuery(0, axiosError())).toBe(true);
    expect(shouldRetryQuery(1, axiosError())).toBe(false);
    expect(shouldRetryQuery(0, new Error("render bug"))).toBe(false);
  });
});
