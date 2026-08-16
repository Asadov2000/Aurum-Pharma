import { beforeEach, describe, expect, it, vi } from "vitest";

const get = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", () => ({ api: { get, post: vi.fn() } }));

import { getFinancialAccount } from "@/features/billing/api";

describe("tenant billing API", () => {
  beforeEach(() => {
    get.mockReset();
  });

  it("reads only the tenant financial projection and forwards cancellation", async () => {
    const signal = new AbortController().signal;
    const account = {
      subscription: null,
      currency: "TJS",
      outstanding_amount: "0.00",
      credit_balance: "0.00",
      invoices: [],
      payments: [],
    } as const;
    get.mockResolvedValueOnce({ data: account });

    await expect(getFinancialAccount(signal)).resolves.toEqual(account);
    expect(get).toHaveBeenCalledWith("/billing/financial-account", { signal });
  });
});
