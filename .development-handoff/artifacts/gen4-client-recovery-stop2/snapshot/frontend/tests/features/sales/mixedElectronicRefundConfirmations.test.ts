vi.mock("@/features/sales/mixedElectronicRefundAccess", () => ({
  mixedElectronicRefundGuard: () => () => {
    if (!session.active) throw new Error("Аккаунт изменился.");
  },
  mixedElectronicRefundAuthority: async (_scope: unknown, check: () => void) => {
    check();
  },
}));
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { SavedMixedElectronicRefundActivation } from "@/features/sales/mixedElectronicRefundActivation";
import {
  readMixedElectronicRefundConfirmations,
  recordMixedElectronicRefundConfirmation,
  type ReconciledMixedElectronicRefund,
} from "@/features/sales/mixedElectronicRefundConfirmations";
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
const activation = (): SavedMixedElectronicRefundActivation => ({
  state: "activation_saved",
  operation_id: id(6),
  parent_sale_id: id(3),
  branch_id: id(4),
  actor_id: id(2),
  register_id: id(9),
  shift_id: id(10),
  activated_at: "2026-10-20T06:00:00.000002Z",
  review: {
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
        { original_payment_id: id(8), method: "card", amount: "1.00", currency: "TJS" },
        { original_payment_id: id(11), method: "qr", amount: "1.99", currency: "TJS" },
      ],
    },
  },
});
const confirmation = () => ({
  original_payment_id: id(8),
  terminal_id: "T-1",
  document_number: "D-1",
});
const partial = (): ReconciledMixedElectronicRefund => ({
  state: "requires_reconciliation",
  activation: activation(),
  confirmations: [
    {
      ...confirmation(),
      method: "card",
      amount: "1.00",
      currency: "TJS",
      recorded_at: "2026-10-20T06:00:00.000003Z",
    },
  ],
  missing_payment_ids: [id(11)],
});
const complete = (): ReconciledMixedElectronicRefund => ({
  ...partial(),
  state: "confirmed_evidence_only",
  missing_payment_ids: [],
  confirmations: [
    ...partial().confirmations,
    {
      original_payment_id: id(11),
      terminal_id: "T-1",
      document_number: "D-2",
      method: "qr",
      amount: "1.99",
      currency: "TJS",
      recorded_at: "2026-10-20T06:00:00.000004Z",
    },
  ],
});
const path = `/sales/${id(3)}/mixed-electronic-refunds/reviews/${id(6)}/confirmations`;
const config = { timeout: 8_000, _auth: { generation: 7, epoch: "original-session" } };
const operations = [
  [
    "record",
    (
      s: ReturnType<typeof scope>,
      r: ReturnType<typeof request>,
      a: SavedMixedElectronicRefundActivation,
    ) => recordMixedElectronicRefundConfirmation(s, r, a, confirmation()),
  ],
  ["read", readMixedElectronicRefundConfirmations],
] as const;
type Mutation = (value: ReconciledMixedElectronicRefund) => unknown;
const malformed: Array<[string, Mutation]> = [
  [
    "noncanonical saved evidence",
    (r) => ({ ...r, confirmations: [{ ...r.confirmations[0], terminal_id: " T-1 " }] }),
  ],
  ["extra top-level field", (r) => ({ ...r, payout: true })],
  ["unknown state", (r) => ({ ...r, state: "posted" })],
  ["wrong activation actor", (r) => ({ ...r, activation: { ...r.activation, actor_id: id(12) } })],
  ["different saved shift", (r) => ({ ...r, activation: { ...r.activation, shift_id: id(12) } })],
  [
    "different saved time",
    (r) => ({ ...r, activation: { ...r.activation, activated_at: "2026-10-20T06:00:00.000001Z" } }),
  ],
  [
    "different nested revision",
    (r) => ({
      ...r,
      activation: {
        ...r.activation,
        review: {
          ...r.activation.review,
          preview: { ...r.activation.review.preview, revision_hash: "b".repeat(64) },
        },
      },
    }),
  ],
  [
    "different nested total",
    (r) => ({
      ...r,
      activation: {
        ...r.activation,
        review: {
          ...r.activation.review,
          preview: { ...r.activation.review.preview, total_amount: "4.00" },
        },
      },
    }),
  ],
  [
    "duplicate confirmed UUID",
    (r) => ({ ...r, confirmations: [r.confirmations[0], r.confirmations[0]] }),
  ],
  [
    "cash UUID",
    (r) => ({ ...r, confirmations: [{ ...r.confirmations[0], original_payment_id: id(7) }] }),
  ],
  [
    "unknown UUID",
    (r) => ({ ...r, confirmations: [{ ...r.confirmations[0], original_payment_id: id(12) }] }),
  ],
  ["wrong method", (r) => ({ ...r, confirmations: [{ ...r.confirmations[0], method: "qr" }] })],
  ["wrong amount", (r) => ({ ...r, confirmations: [{ ...r.confirmations[0], amount: "1.01" }] })],
  ["numeric amount", (r) => ({ ...r, confirmations: [{ ...r.confirmations[0], amount: 1 }] })],
  [
    "noncanonical money",
    (r) => ({ ...r, confirmations: [{ ...r.confirmations[0], amount: "01.00" }] }),
  ],
  [
    "wrong currency",
    (r) => ({ ...r, confirmations: [{ ...r.confirmations[0], currency: "USD" }] }),
  ],
  [
    "extra confirmation field",
    (r) => ({ ...r, confirmations: [{ ...r.confirmations[0], payout: true }] }),
  ],
  [
    "recorded before activation",
    (r) => ({
      ...r,
      confirmations: [{ ...r.confirmations[0], recorded_at: "2026-10-20T06:00:00.000001Z" }],
    }),
  ],
  [
    "invalid timezone",
    (r) => ({
      ...r,
      confirmations: [{ ...r.confirmations[0], recorded_at: "2026-10-20T06:00:00+99:99" }],
    }),
  ],
  [
    "no timezone",
    (r) => ({
      ...r,
      confirmations: [{ ...r.confirmations[0], recorded_at: "2026-10-20T06:00:00" }],
    }),
  ],
  ["missing one original part", (r) => ({ ...r, missing_payment_ids: [] })],
  ["duplicate missing UUID", (r) => ({ ...r, missing_payment_ids: [id(11), id(11)] })],
  ["confirmed UUID also missing", (r) => ({ ...r, missing_payment_ids: [id(8)] })],
  ["unknown missing UUID", (r) => ({ ...r, missing_payment_ids: [id(12)] })],
  ["false complete state", (r) => ({ ...r, state: "confirmed_evidence_only" })],
  ["false partial state", () => ({ ...complete(), state: "requires_reconciliation" })],
  [
    "duplicate document",
    () => {
      const r = complete();
      r.confirmations[1].document_number = "D-1";
      return r;
    },
  ],
];

beforeEach(() => {
  vi.clearAllMocks();
  session.active = true;
});

describe("electronic refund confirmations client", () => {
  it("records one normalized operator confirmation and preserves a partial result", async () => {
    vi.mocked(api.post).mockResolvedValue({ data: partial() });
    const payload = { ...confirmation(), terminal_id: " T-1 ", document_number: " D-1 " };
    expect(
      await recordMixedElectronicRefundConfirmation(scope(), request(), activation(), payload),
    ).toEqual(partial());
    expect(api.post).toHaveBeenCalledWith(path, confirmation(), config);
    expect(api.get).not.toHaveBeenCalled();
  });

  it("reads empty, partial and complete evidence without issuing another command", async () => {
    const empty = { ...partial(), confirmations: [], missing_payment_ids: [id(8), id(11)] };
    for (const result of [empty, partial(), complete()]) {
      vi.mocked(api.get).mockResolvedValueOnce({ data: result });
      expect(
        await readMixedElectronicRefundConfirmations(scope(), request(), activation()),
      ).toEqual(result);
    }
    expect(api.get).toHaveBeenCalledWith(path, config);
    expect(api.post).not.toHaveBeenCalled();
  });

  it("accepts equivalent record time with a different timezone", async () => {
    const result = partial();
    result.confirmations[0].recorded_at = "2026-10-20T09:00:00.000002+03:00";
    vi.mocked(api.get).mockResolvedValue({ data: result });
    expect(await readMixedElectronicRefundConfirmations(scope(), request(), activation())).toEqual(
      result,
    );
  });

  it.each(malformed)("rejects invalid recovery: %s", async (_name, mutate) => {
    vi.mocked(api.get).mockResolvedValue({ data: mutate(partial()) });
    await expect(
      readMixedElectronicRefundConfirmations(scope(), request(), activation()),
    ).rejects.toThrow();
    expect(api.get).toHaveBeenCalledTimes(1);
    expect(api.post).not.toHaveBeenCalled();
  });

  it("requires the POST result to contain the submitted exact evidence", async () => {
    vi.mocked(api.post).mockResolvedValue({
      data: { ...partial(), confirmations: [], missing_payment_ids: [id(8), id(11)] },
    });
    await expect(
      recordMixedElectronicRefundConfirmation(scope(), request(), activation(), confirmation()),
    ).rejects.toThrow();
    const result = partial();
    result.confirmations[0].document_number = "other-document";
    vi.mocked(api.post).mockResolvedValue({ data: result });
    await expect(
      recordMixedElectronicRefundConfirmation(scope(), request(), activation(), confirmation()),
    ).rejects.toThrow();
  });

  it.each([
    ["cash", { ...confirmation(), original_payment_id: id(7) }],
    ["unknown UUID", { ...confirmation(), original_payment_id: id(12) }],
    ["empty", { ...confirmation(), terminal_id: "   " }],
    ["control", { ...confirmation(), terminal_id: "T\u0001-1" }],
    ["DEL", { ...confirmation(), document_number: "D\u007f-1" }],
    ["terminal length", { ...confirmation(), terminal_id: "x".repeat(65) }],
    ["document length", { ...confirmation(), document_number: "x".repeat(129) }],
  ])("rejects invalid submission before network: %s", async (_name, payload) => {
    await expect(
      recordMixedElectronicRefundConfirmation(scope(), request(), activation(), payload),
    ).rejects.toThrow();
    expect(api.post).not.toHaveBeenCalled();
    expect(api.get).not.toHaveBeenCalled();
  });

  it("does not turn an absent submission into a recovery request", async () => {
    await expect(
      recordMixedElectronicRefundConfirmation(
        scope(),
        request(),
        activation(),
        undefined as unknown as ReturnType<typeof confirmation>,
      ),
    ).rejects.toThrow();
    expect(api.post).not.toHaveBeenCalled();
    expect(api.get).not.toHaveBeenCalled();
  });

  it.each(operations)("validates scope and selected marker before %s", async (_name, operation) => {
    await expect(
      operation({ ...scope(), parentSaleId: "../other" }, request(), activation()),
    ).rejects.toThrow();
    const bad = activation();
    bad.review.preview.payments[0].amount = "9.00";
    await expect(operation(scope(), request(), bad)).rejects.toThrow();
    await expect(
      operation(scope(), request(), { ...activation(), register_id: id(12) }),
    ).rejects.toThrow();
    expect(api.post).not.toHaveBeenCalled();
    expect(api.get).not.toHaveBeenCalled();
  });

  it.each(operations)(
    "blocks %s before dispatch for an inactive session",
    async (_name, operation) => {
      session.active = false;
      await expect(operation(scope(), request(), activation())).rejects.toThrow();
      expect(api.post).not.toHaveBeenCalled();
      expect(api.get).not.toHaveBeenCalled();
    },
  );

  it.each(operations)("discards %s after a session change", async (_name, operation) => {
    const respond = async () => {
      session.active = false;
      return { data: partial() };
    };
    vi.mocked(api.post).mockImplementation(respond);
    vi.mocked(api.get).mockImplementation(respond);
    await expect(operation(scope(), request(), activation())).rejects.toThrow();
  });

  it.each(operations)("captures authority before %s dispatch", async (_name, operation) => {
    vi.mocked(api.post).mockResolvedValue({ data: partial() });
    vi.mocked(api.get).mockResolvedValue({ data: partial() });
    await operation(scope(), request(), activation());
    expect(withAuthSession).toHaveBeenCalledWith({ timeout: 8_000 });
    const dispatch =
      vi.mocked(api.post).mock.invocationCallOrder[0] ??
      vi.mocked(api.get).mock.invocationCallOrder[0];
    expect(vi.mocked(withAuthSession).mock.invocationCallOrder[0]).toBeLessThan(dispatch);
  });

  it.each(operations)("snapshots all caller inputs before %s awaits", async (_name, operation) => {
    const s = scope(),
      r = request(),
      a = activation();
    const respond = async () => {
      s.parentSaleId = id(12);
      s.tenantId = id(12);
      r.request.expected_revision_hash = "b".repeat(64);
      a.shift_id = id(12);
      a.review.preview.payments[1].amount = "9.00";
      return { data: partial() };
    };
    vi.mocked(api.post).mockImplementation(respond);
    vi.mocked(api.get).mockImplementation(respond);
    expect(await operation(s, r, a)).toEqual(partial());
  });

  it("snapshots the submitted terminal evidence", async () => {
    const input = confirmation();
    vi.mocked(api.post).mockImplementation(async () => {
      input.document_number = "changed";
      return { data: partial() };
    });
    expect(
      await recordMixedElectronicRefundConfirmation(scope(), request(), activation(), input),
    ).toEqual(partial());
  });

  it.each(operations)("adds no retry loop after a failed %s", async (_name, operation) => {
    const error = new Error("request failed");
    vi.mocked(api.post).mockRejectedValue(error);
    vi.mocked(api.get).mockRejectedValue(error);
    await expect(operation(scope(), request(), activation())).rejects.toBe(error);
    expect(vi.mocked(api.post).mock.calls.length + vi.mocked(api.get).mock.calls.length).toBe(1);
  });
});
