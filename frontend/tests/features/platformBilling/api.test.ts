import { beforeEach, describe, expect, it, vi } from "vitest";

const get = vi.hoisted(() => vi.fn());
const post = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", () => ({ api: { get, post } }));

import {
  activatePlatformPricingPrice,
  cancelPlatformPricingPrice,
  createPlatformPricingPlan,
  createPlatformPricingPrice,
  listPlatformPricingPlans,
  schedulePlatformPricingPrice,
} from "@/features/platformBilling/api";

describe("platform pricing API", () => {
  beforeEach(() => {
    get.mockReset().mockResolvedValue({ data: { items: [], total: 0, page: 1, page_size: 20 } });
    post.mockReset().mockResolvedValue({ data: { item: {}, applied: true } });
  });

  it("uses the protected pricing endpoints without changing financial strings", async () => {
    const operation_id = "123e4567-e89b-42d3-a456-426614174000";
    const signal = new AbortController().signal;
    await listPlatformPricingPlans(2, 20, signal);
    await createPlatformPricingPlan({ operation_id, code: "business", name: "Бизнес" });
    await createPlatformPricingPrice("plan-1", {
      operation_id,
      monthly_price_per_branch: "590.00",
      annual_discount_pct: "20.00",
      audience: "default",
      notice_days: 30,
      change_reason: "Плановое обновление коммерческой цены.",
    });
    await schedulePlatformPricingPrice("price-1", {
      operation_id,
      expected_row_version: 1,
      effective_from: "2026-10-01T00:00:00.000Z",
    });
    await activatePlatformPricingPrice("price-1", {
      operation_id,
      expected_row_version: 2,
    });
    await cancelPlatformPricingPrice("price-1", {
      operation_id,
      expected_row_version: 2,
      reason_code: "commercial_change",
      reason: "Коммерческие условия будут пересмотрены.",
    });

    expect(get).toHaveBeenCalledWith("/admin/billing/plans", {
      params: { page: 2, page_size: 20 },
      signal,
    });
    expect(post).toHaveBeenNthCalledWith(1, "/admin/billing/plans", {
      operation_id,
      code: "business",
      name: "Бизнес",
    });
    expect(post).toHaveBeenNthCalledWith(
      2,
      "/admin/billing/plans/plan-1/prices",
      expect.objectContaining({ monthly_price_per_branch: "590.00" }),
    );
    expect(post).toHaveBeenNthCalledWith(
      3,
      "/admin/billing/prices/price-1/schedule",
      expect.objectContaining({ expected_row_version: 1 }),
    );
    expect(post).toHaveBeenNthCalledWith(4, "/admin/billing/prices/price-1/activate", {
      operation_id,
      expected_row_version: 2,
    });
    expect(post).toHaveBeenNthCalledWith(
      5,
      "/admin/billing/prices/price-1/cancel",
      expect.objectContaining({ reason_code: "commercial_change" }),
    );
  });
});
