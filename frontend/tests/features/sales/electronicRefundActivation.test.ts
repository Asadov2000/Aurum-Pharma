import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  activateElectronicRefundReview,
  readElectronicRefundActivation,
} from "@/features/sales/electronicRefundActivation";
import { api, withAuthSession } from "@/lib/api";

const session = vi.hoisted(() => ({ active: true }));
vi.mock("@/features/pos/posStorageScope", () => ({
  capturePosSessionGuard: vi.fn(() => () => session.active),
}));
vi.mock("@/lib/api", () => ({
  api: { post: vi.fn(), get: vi.fn() },
  withAuthSession: vi.fn((config: object) => ({
    ...config,
    _auth: { generation: 7, epoch: "original-session" },
  })),
}));

const id = (value: number): string => `00000000-0000-4000-8000-${String(value).padStart(12, "0")}`;
const scope = () => ({
  tenantId: id(1),
  userId: id(2),
  parentSaleId: id(3),
  branchId: id(4),
  registerId: id(9),
});
const request = () => ({
  operation_id: id(6),
  request: {
    parent_sale_id: id(3),
    selection: { items: [{ line_id: id(5), sale_format: "unit" as const, quantity: 2 }] },
    expected_total_amount: "3.99",
    expected_revision_hash: "a".repeat(64),
  },
});
const savedReview = () => ({
  state: "saved_review",
  operation_id: id(6),
  saved_at: "2026-10-20T06:00:00.000001Z",
  request: request().request,
  preview: {
    state: "preview",
    parent_sale_id: id(3),
    branch_id: id(4),
    original_receipt_number: "S-1",
    revision_hash: "a".repeat(64),
    total_amount: "3.99",
    currency: "TJS",
    lines: [
      {
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
  },
});
const activation = () => ({
  state: "activation_saved",
  operation_id: id(6),
  parent_sale_id: id(3),
  branch_id: id(4),
  actor_id: id(2),
  register_id: id(9),
  shift_id: id(10),
  activated_at: "2026-10-20T06:00:00.000002Z",
  review: savedReview(),
});
const path = `/sales/${id(3)}/unit-electronic-refunds/reviews/${id(6)}`;
const config = { timeout: 8_000, _auth: { generation: 7, epoch: "original-session" } };
const operations = [
  [
    "activate",
    async (s: ReturnType<typeof scope>, r: ReturnType<typeof request>) =>
      activateElectronicRefundReview(s, r, id(10)),
  ],
  ["read", readElectronicRefundActivation],
] as const;

beforeEach(() => {
  vi.clearAllMocks();
  session.active = true;
});

describe("electronic refund activation client", () => {
  it("activates the reviewed operation with only the chosen shift", async () => {
    const response = activation();
    vi.mocked(api.post).mockResolvedValue({ data: response });
    expect(await activateElectronicRefundReview(scope(), request(), id(10))).toEqual(response);
    expect(api.post).toHaveBeenCalledWith(`${path}/activate`, { shift_id: id(10) }, config);
    expect(api.get).not.toHaveBeenCalled();
  });

  it("recovers the saved shift without sending another activation", async () => {
    const response = { ...activation(), shift_id: id(11) };
    vi.mocked(api.get).mockResolvedValue({ data: response });
    expect(await readElectronicRefundActivation(scope(), request())).toEqual(response);
    expect(api.get).toHaveBeenCalledWith(`${path}/activation`, config);
    expect(api.post).not.toHaveBeenCalled();
  });

  it("accepts the same instant across timezones without losing review fractions", async () => {
    const response = {
      ...activation(),
      activated_at: "2026-10-20T09:00:00.000001+03:00",
    };
    vi.mocked(api.get).mockResolvedValue({ data: response });
    expect(await readElectronicRefundActivation(scope(), request())).toEqual(response);
  });

  it.each([
    ["different operation", { ...activation(), operation_id: id(20) }],
    ["different sale", { ...activation(), parent_sale_id: id(20) }],
    ["different branch", { ...activation(), branch_id: id(20) }],
    ["different actor", { ...activation(), actor_id: id(20) }],
    ["different register", { ...activation(), register_id: id(20) }],
    ["invalid shift UUID", { ...activation(), shift_id: "other-shift" }],
    ["wrong state", { ...activation(), state: "confirmed" }],
    ["extra financial field", { ...activation(), refund_amount: "3.99" }],
    ["missing review", { ...activation(), review: undefined }],
    ["extra nested field", { ...activation(), review: { ...savedReview(), financial_id: id(20) } }],
    ["nested operation", { ...activation(), review: { ...savedReview(), operation_id: id(20) } }],
    [
      "nested revision",
      {
        ...activation(),
        review: {
          ...savedReview(),
          preview: { ...savedReview().preview, revision_hash: "b".repeat(64) },
        },
      },
    ],
    [
      "nested amount",
      {
        ...activation(),
        review: {
          ...savedReview(),
          request: { ...request().request, expected_total_amount: "4.00" },
        },
      },
    ],
    [
      "nested selection",
      {
        ...activation(),
        review: {
          ...savedReview(),
          preview: {
            ...savedReview().preview,
            lines: [{ ...savedReview().preview.lines[0], quantity: 3 }],
          },
        },
      },
    ],
    ["missing timezone", { ...activation(), activated_at: "2026-10-20T06:00:00" }],
    ["invalid offset", { ...activation(), activated_at: "2026-10-20T06:00:00+99:99" }],
    ["before review by one microsecond", { ...activation(), activated_at: "2026-10-20T06:00:00Z" }],
    [
      "before review with different offset",
      { ...activation(), activated_at: "2026-10-20T08:59:59+03:00" },
    ],
    [
      "invalid review offset",
      { ...activation(), review: { ...savedReview(), saved_at: "2026-10-20T06:00:00+99:99" } },
    ],
  ])("rejects an invalid recovered marker: %s", async (_name, response) => {
    vi.mocked(api.get).mockResolvedValue({ data: response });
    await expect(readElectronicRefundActivation(scope(), request())).rejects.toThrow();
    expect(api.get).toHaveBeenCalledTimes(1);
    expect(api.post).not.toHaveBeenCalled();
  });

  it("rejects a POST result for a different shift", async () => {
    vi.mocked(api.post).mockResolvedValue({ data: { ...activation(), shift_id: id(11) } });
    await expect(activateElectronicRefundReview(scope(), request(), id(10))).rejects.toThrow();
  });

  it("rejects invalid or absent activation shifts without falling back to GET", async () => {
    for (const shiftId of ["../other", undefined as unknown as string]) {
      await expect(async () =>
        activateElectronicRefundReview(scope(), request(), shiftId),
      ).rejects.toThrow();
    }
    expect(api.post).not.toHaveBeenCalled();
    expect(api.get).not.toHaveBeenCalled();
  });

  it.each(operations)("validates all inputs before %s", async (_name, operation) => {
    await expect(operation({ ...scope(), parentSaleId: "../other" }, request())).rejects.toThrow();
    await expect(operation({ ...scope(), tenantId: "bad" }, request())).rejects.toThrow();
    await expect(operation({ ...scope(), registerId: "bad" }, request())).rejects.toThrow();
    const otherSale = request();
    otherSale.request.parent_sale_id = id(20);
    await expect(operation(scope(), otherSale)).rejects.toThrow();
    await expect(operation(scope(), { ...request(), operation_id: "bad" })).rejects.toThrow();
    const badRevision = request();
    badRevision.request.expected_revision_hash = "wrong";
    await expect(operation(scope(), badRevision)).rejects.toThrow();
    expect(api.post).not.toHaveBeenCalled();
    expect(api.get).not.toHaveBeenCalled();
  });

  it.each(operations)("blocks inactive sessions before %s", async (_name, operation) => {
    session.active = false;
    await expect(operation(scope(), request())).rejects.toThrow();
    expect(api.post).not.toHaveBeenCalled();
    expect(api.get).not.toHaveBeenCalled();
  });

  it.each(operations)("discards %s after a session change", async (_name, operation) => {
    const changeSession = async () => {
      session.active = false;
      return { data: activation() };
    };
    vi.mocked(api.post).mockImplementation(changeSession);
    vi.mocked(api.get).mockImplementation(changeSession);
    await expect(operation(scope(), request())).rejects.toThrow();
  });

  it.each(operations)("captures immutable inputs during %s", async (_name, operation) => {
    const originalScope = scope();
    const originalRequest = request();
    const mutateCallerInput = async () => {
      originalScope.tenantId = id(20);
      originalScope.parentSaleId = id(20);
      originalScope.registerId = id(20);
      originalRequest.operation_id = id(20);
      originalRequest.request.parent_sale_id = id(20);
      originalRequest.request.expected_revision_hash = "b".repeat(64);
      originalRequest.request.selection.items[0].quantity = 3;
      return { data: activation() };
    };
    vi.mocked(api.post).mockImplementation(mutateCallerInput);
    vi.mocked(api.get).mockImplementation(mutateCallerInput);
    expect(await operation(originalScope, originalRequest)).toEqual(activation());
  });

  it.each(operations)("captures authority before dispatching %s", async (_name, operation) => {
    vi.mocked(api.post).mockResolvedValue({ data: activation() });
    vi.mocked(api.get).mockResolvedValue({ data: activation() });
    await operation(scope(), request());
    expect(withAuthSession).toHaveBeenCalledWith({ timeout: 8_000 });
    const dispatch =
      vi.mocked(api.post).mock.invocationCallOrder[0] ??
      vi.mocked(api.get).mock.invocationCallOrder[0];
    expect(vi.mocked(withAuthSession).mock.invocationCallOrder[0]).toBeLessThan(dispatch);
  });

  it.each(operations)("adds no retry loop after a failed %s", async (_name, operation) => {
    const error = new Error("request failed");
    vi.mocked(api.post).mockRejectedValue(error);
    vi.mocked(api.get).mockRejectedValue(error);
    await expect(operation(scope(), request())).rejects.toBe(error);
    expect(vi.mocked(api.post).mock.calls.length + vi.mocked(api.get).mock.calls.length).toBe(1);
  });
});
