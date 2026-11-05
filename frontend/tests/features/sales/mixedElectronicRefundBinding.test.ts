vi.mock("@/features/sales/mixedElectronicRefundAccess", () => ({
  mixedElectronicRefundGuard: () => () => {
    if (!session.active) throw new Error("Аккаунт изменился.");
  },
  mixedElectronicRefundAuthority: async (_scope: unknown, check: () => void) => {
    check();
    if (session.changeDuringAuthority)
      queueMicrotask(() => {
        session.active = false;
      });
  },
}));
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  bindMixedElectronicRefundReview,
  cancelMixedElectronicRefundBinding,
  readMixedElectronicRefundBinding,
} from "@/features/sales/mixedElectronicRefundBinding";
import { api, withAuthSession } from "@/lib/api";

const session = vi.hoisted(() => ({ active: true, changeDuringAuthority: false }));
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
    selection: {
      items: [
        {
          inventory_kind: "physical_unit" as const,
          line_id: id(5),
          sale_format: "unit" as const,
          quantity: 2,
        },
      ],
    },
    expected_total_amount: "3.99",
    expected_revision_hash: "a".repeat(64),
  },
});
const bound = () => ({
  state: "bound" as const,
  operation_id: id(6),
  parent_sale_id: id(3),
  review_revision_hash: "a".repeat(64),
  bound_at: "2026-10-20T06:00:00.000001Z",
  cancelled_at: null,
});
const cancelled = () => ({
  ...bound(),
  state: "cancelled" as const,
  bound_at: "2026-10-20T06:00:00.000001Z" as string | null,
  cancelled_at: "2026-10-20T06:00:00.000002Z",
});
const path = `/sales/${id(3)}/mixed-electronic-refunds/reviews/${id(6)}`;
const config = { timeout: 8_000, _auth: { generation: 7, epoch: "original-session" } };
const operations = [
  ["bind", bindMixedElectronicRefundReview, bound],
  ["cancel", cancelMixedElectronicRefundBinding, cancelled],
  ["read", readMixedElectronicRefundBinding, bound],
] as const;

beforeEach(() => {
  vi.clearAllMocks();
  session.active = true;
  session.changeDuringAuthority = false;
});

describe("electronic refund binding client", () => {
  it.each([bindMixedElectronicRefundReview, cancelMixedElectronicRefundBinding])(
    "does not send a command after context changes while authority resolves",
    async (command) => {
      session.changeDuringAuthority = true;
      await expect(command(scope(), request())).rejects.toThrow("Аккаунт");
      expect(api.post).not.toHaveBeenCalled();
    },
  );
  it("binds and cancels the reviewed operation without a financial request body", async () => {
    vi.mocked(api.post).mockResolvedValueOnce({ data: bound() });
    expect(await bindMixedElectronicRefundReview(scope(), request())).toEqual(bound());
    expect(api.post).toHaveBeenNthCalledWith(1, `${path}/bind`, undefined, config);
    vi.mocked(api.post).mockResolvedValueOnce({ data: cancelled() });
    expect(await cancelMixedElectronicRefundBinding(scope(), request())).toEqual(cancelled());
    expect(api.post).toHaveBeenNthCalledWith(2, `${path}/cancel-binding`, undefined, config);
    expect(api.get).not.toHaveBeenCalled();
  });

  it("reads both durable states without issuing a command", async () => {
    for (const response of [bound(), cancelled()]) {
      vi.mocked(api.get).mockResolvedValueOnce({ data: response });
      expect(await readMixedElectronicRefundBinding(scope(), request())).toEqual(response);
    }
    expect(api.get).toHaveBeenCalledWith(`${path}/binding`, config);
    expect(api.post).not.toHaveBeenCalled();
  });

  it("accepts final cancellation before an initial binding", async () => {
    const response = { ...cancelled(), bound_at: null };
    vi.mocked(api.post).mockResolvedValue({ data: response });
    expect(await cancelMixedElectronicRefundBinding(scope(), request())).toEqual(response);
  });

  it("compares equivalent timezone offsets and preserves timestamp precision", async () => {
    const response = {
      ...cancelled(),
      bound_at: "2026-10-20T09:00:00.000002+03:00",
      cancelled_at: "2026-10-20T06:00:00.000002Z",
    };
    vi.mocked(api.get).mockResolvedValue({ data: response });
    expect(await readMixedElectronicRefundBinding(scope(), request())).toEqual(response);
  });

  it.each([
    ["different operation", { ...bound(), operation_id: id(9) }],
    ["different sale", { ...bound(), parent_sale_id: id(9) }],
    ["different review", { ...bound(), review_revision_hash: "b".repeat(64) }],
    ["extra financial field", { ...bound(), amount: "3.99" }],
    ["unknown state", { ...bound(), state: "activated" }],
    ["missing binding timestamp", { ...bound(), bound_at: null }],
    ["cancel timestamp on bound state", { ...bound(), cancelled_at: cancelled().cancelled_at }],
    ["missing cancellation timestamp", { ...cancelled(), cancelled_at: null }],
    ["timezone missing", { ...bound(), bound_at: "2026-10-20T06:00:00" }],
    ["invalid offset", { ...cancelled(), cancelled_at: "2026-10-20T06:00:00+99:99" }],
    [
      "cancellation before binding by one microsecond",
      {
        ...cancelled(),
        bound_at: "2026-10-20T06:00:00.000002Z",
        cancelled_at: "2026-10-20T06:00:00.000001Z",
      },
    ],
    [
      "earlier cancellation with a different timezone offset",
      {
        ...cancelled(),
        bound_at: "2026-10-20T06:00:00Z",
        cancelled_at: "2026-10-20T08:00:00+03:00",
      },
    ],
  ])("rejects an invalid read response: %s", async (_name, response) => {
    vi.mocked(api.get).mockResolvedValue({ data: response });
    await expect(readMixedElectronicRefundBinding(scope(), request())).rejects.toThrow();
    expect(api.get).toHaveBeenCalledTimes(1);
    expect(api.post).not.toHaveBeenCalled();
  });

  it("rejects a state belonging to the other command", async () => {
    vi.mocked(api.post).mockResolvedValueOnce({ data: cancelled() });
    await expect(bindMixedElectronicRefundReview(scope(), request())).rejects.toThrow();
    vi.mocked(api.post).mockResolvedValueOnce({ data: bound() });
    await expect(cancelMixedElectronicRefundBinding(scope(), request())).rejects.toThrow();
  });

  it.each(operations)("validates scope and request before %s", async (_name, operation) => {
    await expect(operation({ ...scope(), parentSaleId: "../other" }, request())).rejects.toThrow();
    await expect(operation({ ...scope(), tenantId: "bad" }, request())).rejects.toThrow();
    await expect(operation(scope(), { ...request(), operation_id: "../other" })).rejects.toThrow();
    const otherSale = request();
    otherSale.request.parent_sale_id = id(9);
    await expect(operation(scope(), otherSale)).rejects.toThrow();
    const badRevision = request();
    badRevision.request.expected_revision_hash = "wrong";
    await expect(operation(scope(), badRevision)).rejects.toThrow();
    expect(api.post).not.toHaveBeenCalled();
    expect(api.get).not.toHaveBeenCalled();
  });

  it.each(operations)(
    "blocks %s before a request when the session is inactive",
    async (_name, operation) => {
      session.active = false;
      await expect(operation(scope(), request())).rejects.toThrow();
      expect(api.post).not.toHaveBeenCalled();
      expect(api.get).not.toHaveBeenCalled();
    },
  );

  it.each(operations)(
    "discards %s after the session changes",
    async (_name, operation, response) => {
      const changeSession = async () => {
        session.active = false;
        return { data: response() };
      };
      vi.mocked(api.post).mockImplementation(changeSession);
      vi.mocked(api.get).mockImplementation(changeSession);
      await expect(operation(scope(), request())).rejects.toThrow();
    },
  );

  it.each(operations)(
    "uses immutable input snapshots during %s",
    async (_name, operation, response) => {
      const originalScope = scope();
      const originalRequest = request();
      const mutateCallerInput = async () => {
        originalScope.parentSaleId = id(9);
        originalScope.tenantId = id(9);
        originalRequest.operation_id = id(9);
        originalRequest.request.parent_sale_id = id(9);
        originalRequest.request.expected_revision_hash = "b".repeat(64);
        return { data: response() };
      };
      vi.mocked(api.post).mockImplementation(mutateCallerInput);
      vi.mocked(api.get).mockImplementation(mutateCallerInput);
      expect(await operation(originalScope, originalRequest)).toEqual(response());
    },
  );

  it.each(operations)(
    "captures request authority before dispatching %s",
    async (_name, operation, response) => {
      vi.mocked(api.post).mockResolvedValue({ data: response() });
      vi.mocked(api.get).mockResolvedValue({ data: response() });
      await operation(scope(), request());
      expect(withAuthSession).toHaveBeenCalledWith({ timeout: 8_000 });
      const dispatch =
        vi.mocked(api.post).mock.invocationCallOrder[0] ??
        vi.mocked(api.get).mock.invocationCallOrder[0];
      expect(vi.mocked(withAuthSession).mock.invocationCallOrder[0]).toBeLessThan(dispatch);
    },
  );

  it.each(operations)(
    "does not automatically retry a failed %s request",
    async (_name, operation) => {
      const error = new Error("request failed");
      vi.mocked(api.post).mockRejectedValue(error);
      vi.mocked(api.get).mockRejectedValue(error);
      await expect(operation(scope(), request())).rejects.toBe(error);
      expect(vi.mocked(api.post).mock.calls.length + vi.mocked(api.get).mock.calls.length).toBe(1);
    },
  );
});
