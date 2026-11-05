import { beforeEach, describe, expect, it, vi } from "vitest";

import { requiresSalePrescription } from "@/features/pos/dispensingPreflight";

const get = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", () => ({ api: { get } }));

describe("sale-scoped dispensing preflight", () => {
  beforeEach(() => {
    get.mockReset();
  });

  it.each([false, true])("reads only the current sale and preserves Rx=%s", async (required) => {
    get.mockResolvedValue({ data: { sale_id: "sale/id", requires_prescription: required } });
    await expect(requiresSalePrescription("sale/id")).resolves.toBe(required);
    expect(get).toHaveBeenCalledExactlyOnceWith(
      "/sales/sale%2Fid/physical-cart/dispensing-preflight",
      { timeout: 8_000 },
    );
  });

  it.each([
    null,
    {},
    { sale_id: "other", requires_prescription: false },
    { sale_id: "sale-1", requires_prescription: "false" },
    { sale_id: "sale-1" },
  ])("fails closed for an untrusted response %j", async (data) => {
    get.mockResolvedValue({ data });
    await expect(requiresSalePrescription("sale-1")).rejects.toThrow("Оплата ещё не начата");
  });

  it("propagates denial without falling back to the global catalog", async () => {
    get.mockRejectedValue(new Error("403"));
    await expect(requiresSalePrescription("sale-1")).rejects.toThrow("403");
    expect(get).toHaveBeenCalledTimes(1);
  });
});
