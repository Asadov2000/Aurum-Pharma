vi.mock("@/features/sales/mixedElectronicRefundAccess", () => ({
  mixedElectronicRefundGuard: () => () => {
    if (!session.active) throw new Error("Аккаунт изменился.");
  },
  mixedElectronicRefundAuthority: async (_scope: unknown, check: () => void) => {
    check();
  },
}));
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  mixedElectronicRefundSaveRequestSchema,
  mixedElectronicRefundSelectionSchema,
  quoteMixedElectronicRefundReview,
  readMixedElectronicRefundReview,
  saveMixedElectronicRefundReview,
} from "@/features/sales/mixedElectronicRefundReview";
import { api } from "@/lib/api";

const session = vi.hoisted(() => ({ active: true }));
vi.mock("@/features/pos/posStorageScope", () => ({
  capturePosSessionGuard: vi.fn(() => () => session.active),
}));
vi.mock("@/lib/api", () => ({
  api: { post: vi.fn(), get: vi.fn() },
  withAuthSession: (config: object) => config,
}));

const id = (value: number): string => `00000000-0000-4000-8000-${String(value).padStart(12, "0")}`;
const scope = {
  tenantId: id(1),
  userId: id(2),
  parentSaleId: id(3),
  branchId: id(4),
  registerId: id(9),
};
const selection = () => ({
  items: [
    {
      inventory_kind: "physical_unit" as const,
      line_id: id(5),
      sale_format: "unit" as const,
      quantity: 2,
    },
  ],
});
const request = () => ({
  operation_id: id(6),
  request: {
    parent_sale_id: scope.parentSaleId,
    selection: selection(),
    expected_total_amount: "3.99",
    expected_revision_hash: "a".repeat(64),
  },
});
const preview = () => ({
  state: "preview",
  parent_sale_id: scope.parentSaleId,
  branch_id: scope.branchId,
  original_receipt_number: "S-1",
  revision_hash: "a".repeat(64),
  total_amount: "3.99",
  currency: "TJS",
  lines: [
    {
      inventory_kind: "physical_unit" as const,
      line_id: id(5),
      sale_format: "unit",
      quantity: 2,
      base_units: 2,
      offset: 0,
      gross_amount: "4.00",
      discount_amount: "0.01",
      net_amount: "3.99",
    },
  ],
  payments: [
    { original_payment_id: id(7), method: "cash", amount: "1.00", currency: "TJS" },
    { original_payment_id: id(8), method: "card", amount: "2.99", currency: "TJS" },
  ],
});
const saved = () => ({
  state: "saved_review",
  operation_id: id(6),
  saved_at: "2026-10-20T04:00:00Z",
  request: request().request,
  preview: preview(),
});
const path = `/sales/${scope.parentSaleId}/mixed-electronic-refunds`;

beforeEach(() => {
  vi.clearAllMocks();
  session.active = true;
});

describe("electronic refund review client", () => {
  it("quotes the exact selection and preserves original payment identities", async () => {
    const response = preview();
    vi.mocked(api.post).mockResolvedValue({ data: response });
    expect(await quoteMixedElectronicRefundReview(scope, selection())).toEqual(response);
    expect(api.post).toHaveBeenCalledWith(`${path}/quote`, selection(), { timeout: 8_000 });
  });

  it("preserves four balanced original payment parts, including repeated methods", async () => {
    const response = preview();
    response.payments = [
      { original_payment_id: id(7), method: "cash", amount: "1.00", currency: "TJS" },
      { original_payment_id: id(8), method: "card", amount: "1.00", currency: "TJS" },
      { original_payment_id: id(9), method: "card", amount: "1.00", currency: "TJS" },
      { original_payment_id: id(10), method: "qr", amount: "0.99", currency: "TJS" },
    ];
    vi.mocked(api.post).mockResolvedValue({ data: response });
    expect(await quoteMixedElectronicRefundReview(scope, selection())).toEqual(response);
  });

  it("saves only the reviewed request and reads the same immutable operation", async () => {
    const response = saved();
    vi.mocked(api.post).mockResolvedValue({ data: response });
    vi.mocked(api.get).mockResolvedValue({ data: response });
    expect(await saveMixedElectronicRefundReview(scope, request())).toEqual(response);
    expect(api.post).toHaveBeenCalledWith(`${path}/reviews`, request(), { timeout: 8_000 });
    expect(await readMixedElectronicRefundReview(scope, request())).toEqual(response);
    expect(api.get).toHaveBeenCalledWith(`${path}/reviews/${id(6)}`, { timeout: 8_000 });
    expect(api.post).toHaveBeenCalledTimes(1);
  });

  it.each([
    ["extra field", (p: ReturnType<typeof preview>) => ({ ...p, physical_cost_amount: "1.00" })],
    ["money number", (p: ReturnType<typeof preview>) => ({ ...p, total_amount: 3.99 })],
    ["exponent money", (p: ReturnType<typeof preview>) => ({ ...p, total_amount: "3.99e0" })],
    [
      "line imbalance",
      (p: ReturnType<typeof preview>) => ({ ...p, lines: [{ ...p.lines[0], net_amount: "3.98" }] }),
    ],
    [
      "payment imbalance",
      (p: ReturnType<typeof preview>) => ({
        ...p,
        payments: [{ ...p.payments[1], amount: "3.98" }],
      }),
    ],
    [
      "duplicate line",
      (p: ReturnType<typeof preview>) => ({ ...p, lines: [p.lines[0], p.lines[0]] }),
    ],
    [
      "duplicate payment",
      (p: ReturnType<typeof preview>) => ({
        ...p,
        payments: [
          { ...p.payments[0], amount: "1.00" },
          { ...p.payments[1], original_payment_id: p.payments[0].original_payment_id },
        ],
      }),
    ],
    [
      "negative amount",
      (p: ReturnType<typeof preview>) => ({
        ...p,
        payments: [{ ...p.payments[1], amount: "-3.99" }],
      }),
    ],
    [
      "unsafe quantity",
      (p: ReturnType<typeof preview>) => ({
        ...p,
        lines: [{ ...p.lines[0], quantity: Number.MAX_SAFE_INTEGER + 1 }],
      }),
    ],
    ["wrong currency", (p: ReturnType<typeof preview>) => ({ ...p, currency: "USD" })],
    ["wrong sale", (p: ReturnType<typeof preview>) => ({ ...p, parent_sale_id: id(99) })],
    ["wrong branch", (p: ReturnType<typeof preview>) => ({ ...p, branch_id: id(99) })],
    [
      "wrong selection",
      (p: ReturnType<typeof preview>) => ({ ...p, lines: [{ ...p.lines[0], line_id: id(99) }] }),
    ],
  ] as const)("rejects a quote with %s", async (_name, mutate) => {
    vi.mocked(api.post).mockResolvedValue({ data: mutate(preview()) });
    await expect(quoteMixedElectronicRefundReview(scope, selection())).rejects.toThrow();
  });

  it("accepts balanced cash-only and zero quotes without treating them as saved reviews", async () => {
    const cash = preview();
    cash.payments = [{ ...cash.payments[0], amount: "3.99" }];
    vi.mocked(api.post).mockResolvedValue({ data: cash });
    expect(await quoteMixedElectronicRefundReview(scope, selection())).toEqual(cash);
    const zero = preview();
    zero.total_amount = "0.00";
    zero.lines[0].discount_amount = "4.00";
    zero.lines[0].net_amount = "0.00";
    zero.payments = [];
    vi.mocked(api.post).mockResolvedValue({ data: zero });
    expect(await quoteMixedElectronicRefundReview(scope, selection())).toEqual(zero);
  });

  it("balances maximum supported money using exact cents", async () => {
    const response = preview();
    response.total_amount = "999999999999.99";
    response.lines[0].gross_amount = response.total_amount;
    response.lines[0].discount_amount = "0.00";
    response.lines[0].net_amount = response.total_amount;
    response.payments = [{ ...response.payments[1], amount: response.total_amount }];
    vi.mocked(api.post).mockResolvedValue({ data: response });
    expect((await quoteMixedElectronicRefundReview(scope, selection())).total_amount).toBe(
      response.total_amount,
    );
  });

  it.each([
    ["operation", (s: ReturnType<typeof saved>) => ({ ...s, operation_id: id(99) })],
    [
      "saved request",
      (s: ReturnType<typeof saved>) => ({
        ...s,
        request: { ...s.request, expected_total_amount: "4.00" },
      }),
    ],
    [
      "revision",
      (s: ReturnType<typeof saved>) => ({
        ...s,
        preview: { ...s.preview, revision_hash: "b".repeat(64) },
      }),
    ],
    [
      "cash-only preview",
      (s: ReturnType<typeof saved>) => ({
        ...s,
        preview: { ...s.preview, payments: [{ ...s.preview.payments[0], amount: "3.99" }] },
      }),
    ],
    [
      "zero preview",
      (s: ReturnType<typeof saved>) => ({
        ...s,
        request: { ...s.request, expected_total_amount: "0.00" },
        preview: {
          ...s.preview,
          total_amount: "0.00",
          lines: [{ ...s.preview.lines[0], discount_amount: "4.00", net_amount: "0.00" }],
          payments: [],
        },
      }),
    ],
    ["saved time", (s: ReturnType<typeof saved>) => ({ ...s, saved_at: "yesterday" })],
    [
      "unknown field",
      (s: ReturnType<typeof saved>) => ({ ...s, activated_at: "2026-10-20T04:00:00Z" }),
    ],
  ] as const)("rejects saved/read results with mismatched %s", async (_name, mutate) => {
    const response = mutate(saved());
    vi.mocked(api.post).mockResolvedValue({ data: response });
    vi.mocked(api.get).mockResolvedValue({ data: response });
    await expect(saveMixedElectronicRefundReview(scope, request())).rejects.toThrow();
    await expect(readMixedElectronicRefundReview(scope, request())).rejects.toThrow();
  });

  it("rejects invalid selection and a different source request before network", async () => {
    expect(mixedElectronicRefundSelectionSchema.safeParse({ items: [] }).success).toBe(false);
    expect(
      mixedElectronicRefundSelectionSchema.safeParse({
        items: [...selection().items, ...selection().items],
      }).success,
    ).toBe(false);
    expect(
      mixedElectronicRefundSaveRequestSchema.safeParse({ ...request(), operation_id: "bad" })
        .success,
    ).toBe(false);
    const wrongSource = { ...request(), request: { ...request().request, parent_sale_id: id(99) } };
    await expect(saveMixedElectronicRefundReview(scope, wrongSource)).rejects.toThrow();
    await expect(readMixedElectronicRefundReview(scope, wrongSource)).rejects.toThrow();
    expect(api.post).not.toHaveBeenCalled();
    expect(api.get).not.toHaveBeenCalled();
  });

  it("rejects malformed scope before building a resource URL", async () => {
    const bad = { ...scope, parentSaleId: "../wrong-sale" };
    await expect(quoteMixedElectronicRefundReview(bad, selection())).rejects.toThrow();
    await expect(saveMixedElectronicRefundReview(bad, request())).rejects.toThrow();
    await expect(readMixedElectronicRefundReview(bad, request())).rejects.toThrow();
    expect(api.post).not.toHaveBeenCalled();
    expect(api.get).not.toHaveBeenCalled();
  });

  it("rejects an inactive session before issuing any request", async () => {
    session.active = false;
    await expect(quoteMixedElectronicRefundReview(scope, selection())).rejects.toThrow();
    await expect(saveMixedElectronicRefundReview(scope, request())).rejects.toThrow();
    await expect(readMixedElectronicRefundReview(scope, request())).rejects.toThrow();
    expect(api.post).not.toHaveBeenCalled();
    expect(api.get).not.toHaveBeenCalled();
  });

  it("discards every response after a session change during the request", async () => {
    vi.mocked(api.post).mockImplementation(async () => {
      session.active = false;
      return { data: preview() };
    });
    await expect(quoteMixedElectronicRefundReview(scope, selection())).rejects.toThrow();
    session.active = true;
    vi.mocked(api.post).mockImplementation(async () => {
      session.active = false;
      return { data: saved() };
    });
    await expect(saveMixedElectronicRefundReview(scope, request())).rejects.toThrow();
    session.active = true;
    vi.mocked(api.get).mockImplementation(async () => {
      session.active = false;
      return { data: saved() };
    });
    await expect(readMixedElectronicRefundReview(scope, request())).rejects.toThrow();
  });
});
