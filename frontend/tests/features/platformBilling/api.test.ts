import { beforeEach, describe, expect, it, vi } from "vitest";

const get = vi.hoisted(() => vi.fn());
const post = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", () => ({ api: { get, post } }));

import {
  activatePlatformPricingPrice,
  approvePlatformBankPayment,
  cancelPlatformPricingPrice,
  createPlatformBankPaymentReview,
  createPlatformPricingPlan,
  createPlatformPricingPrice,
  getPlatformFinancialAccount,
  listPlatformBillingTenants,
  listPlatformPaymentApprovalQueue,
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

describe("platform financial kernel API", () => {
  beforeEach(() => {
    get.mockReset();
    post.mockReset();
  });

  it("reads a tenant financial account with request cancellation support", async () => {
    const signal = new AbortController().signal;
    const account = {
      tenant_id: "tenant-1",
      currency: "TJS",
      outstanding_amount: "590.00",
      credit_balance: "0.00",
      invoices: [],
      payments: [],
      journal_balanced: true,
    } as const;
    get.mockResolvedValueOnce({ data: account });

    await expect(getPlatformFinancialAccount("tenant-1", signal)).resolves.toEqual(account);
    expect(get).toHaveBeenCalledWith("/admin/billing/tenants/tenant-1/financial-account", {
      signal,
    });
  });

  it("lists billing tenants and the protected approval queue", async () => {
    const signal = new AbortController().signal;
    get.mockResolvedValue({ data: { items: [], total: 0, page: 1, page_size: 20 } });

    await listPlatformBillingTenants({ q: "Шифо", page: 2, page_size: 20 }, signal);
    await listPlatformPaymentApprovalQueue("tenant-1", 3, 20, signal);

    expect(get).toHaveBeenNthCalledWith(1, "/admin/billing/tenants", {
      params: { q: "Шифо", page: 2, page_size: 20 },
      signal,
    });
    expect(get).toHaveBeenNthCalledWith(2, "/admin/billing/tenants/tenant-1/payment-reviews", {
      params: { page: 3, page_size: 20 },
      signal,
    });
  });

  it("submits review and approval commands without changing financial strings", async () => {
    const reviewPayload = {
      operation_id: "123e4567-e89b-42d3-a456-426614174000",
      target_invoice_id: "invoice-1",
      amount: "590.00",
      paid_at: "2026-08-14T08:30:00.000Z",
      recipient_account_key: "bank.primary.tjs",
      external_reference: "BANK-REF-001",
    };
    const approvePayload = {
      operation_id: "123e4567-e89b-42d3-a456-426614174001",
      expected_row_version: 1,
    };
    post
      .mockResolvedValueOnce({ data: { item: { review_id: "review-1" }, applied: true } })
      .mockResolvedValueOnce({ data: { item: { payment_id: "payment-1" }, applied: true } });

    await createPlatformBankPaymentReview("tenant-1", reviewPayload);
    await approvePlatformBankPayment("tenant-1", "review-1", approvePayload);

    expect(post).toHaveBeenNthCalledWith(
      1,
      "/admin/billing/tenants/tenant-1/payment-reviews",
      reviewPayload,
    );
    expect(post).toHaveBeenNthCalledWith(
      2,
      "/admin/billing/tenants/tenant-1/payment-reviews/review-1/approve",
      approvePayload,
    );
  });
});
