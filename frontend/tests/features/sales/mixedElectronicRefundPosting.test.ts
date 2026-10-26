import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ReconciledMixedElectronicRefund } from "@/features/sales/mixedElectronicRefundConfirmations";
import {
  postMixedElectronicRefund,
  readMixedElectronicRefundPosting,
  type PostedMixedElectronicRefund,
} from "@/features/sales/mixedElectronicRefundPosting";
import { api, withAuthSession } from "@/lib/api";
import { getCurrentShift } from "@/features/pos/api";

const session = vi.hoisted(() => ({ active: true }));
vi.mock("@/features/auth/logoutState", () => ({ getAuthEpoch: () => "test-epoch" }));
vi.mock("@/stores/auth", () => ({
  useAuthStore: {
    getState: () => ({
      accessToken: "test-access",
      user: {
        is_developer: false,
        permissions: ["pos.refund", "pos.refund_external_confirm"],
        permission_scopes: { "pos.refund": null, "pos.refund_external_confirm": null },
      },
    }),
  },
}));
vi.mock("@/features/pos/api", () => ({
  getCurrentShift: vi.fn(async (registerId: string) => ({
    id: id(12),
    status: "open",
    opened_by_user_id: id(2),
    register_id: registerId,
    branch_id: id(4),
    currency: "TJS",
  })),
}));
vi.mock("@/features/pos/posStorageScope", () => ({
  capturePosSessionGuard: vi.fn(() => () => session.active),
}));
vi.mock("@/lib/api", () => ({
  api: { post: vi.fn(), get: vi.fn() },
  withAuthSession: vi.fn((config: object) => ({
    ...config,
    _auth: { generation: 7, epoch: "original" },
  })),
}));
const id = (n: number): string => `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
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
const details = () => ({
  shift_id: id(12),
  reason_code: "other" as const,
  comment: "Operator reason",
});
const evidence = (): ReconciledMixedElectronicRefund => ({
  state: "confirmed_evidence_only",
  missing_payment_ids: [],
  activation: {
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
  },
  confirmations: [
    {
      original_payment_id: id(8),
      method: "card",
      amount: "1.00",
      currency: "TJS",
      terminal_id: "T-1",
      document_number: "D-1",
      recorded_at: "2026-10-20T06:00:00.000003Z",
    },
    {
      original_payment_id: id(11),
      method: "qr",
      amount: "1.99",
      currency: "TJS",
      terminal_id: "T-1",
      document_number: "D-2",
      recorded_at: "2026-10-20T06:00:00.000004Z",
    },
  ],
});
const posted = (): PostedMixedElectronicRefund => ({
  state: "posted_refund",
  operation_id: id(6),
  parent_sale_id: id(3),
  sale_id: id(20),
  tenant_id: id(1),
  branch_id: id(4),
  register_id: id(9),
  shift_id: id(12),
  actor_id: id(2),
  total_amount: "3.99",
  physical_cost_amount: "1.25",
  receipt: {
    schema_version: 4,
    document_kind: "mixed_electronic_refund_receipt",
    parent_sale_id: id(3),
    parent_receipt_number: "S-1",
    receipt_number: "R-1",
    receipt_sequence: 1,
    completed_at: "2026-10-20T06:00:00.000005Z",
    is_test: false,
    total_amount: "3.99",
    currency: "TJS",
    lines: [
      {
        inventory_kind: "physical_unit" as const,
        original_position: 1,
        original_line_id: id(5),
        position: 1,
        name: "Historical item",
        packaging: {
          version: 2,
          unit: "tablet",
          units_per_pack: 10,
          units_per_blister: 5,
          dispense_step: 1,
        },
        original_sale_format: "pack",
        original_retail_unit_price: "20.00",
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
      {
        id: id(21),
        original_payment_id: id(7),
        method: "cash",
        amount: "1.00",
        currency: "TJS",
        terminal_id: null,
        document_number: null,
      },
      {
        id: id(22),
        original_payment_id: id(8),
        method: "card",
        amount: "1.00",
        currency: "TJS",
        terminal_id: "T-1",
        document_number: "D-1",
      },
      {
        id: id(23),
        original_payment_id: id(11),
        method: "qr",
        amount: "1.99",
        currency: "TJS",
        terminal_id: "T-1",
        document_number: "D-2",
      },
    ],
  },
});
const path = `/sales/${id(3)}/mixed-electronic-refunds/reviews/${id(6)}`;
const config = { timeout: 8_000, _auth: { generation: 7, epoch: "original" } };
const operations = [
  [
    "post",
    (
      s: ReturnType<typeof scope>,
      r: ReturnType<typeof request>,
      e: ReconciledMixedElectronicRefund,
    ) => postMixedElectronicRefund(s, r, e, details()),
  ],
  ["read", readMixedElectronicRefundPosting],
] as const;
type Mutation = (r: PostedMixedElectronicRefund) => unknown;
const malformed: Array<[string, Mutation]> = [
  ["evidence-only state", (r) => ({ ...r, state: "confirmed_evidence_only" })],
  ["unknown operation", (r) => ({ ...r, operation_id: id(99) })],
  ["wrong parent sale", (r) => ({ ...r, parent_sale_id: id(99) })],
  ["original sale reused", (r) => ({ ...r, sale_id: id(3) })],
  ["invalid new sale UUID", (r) => ({ ...r, sale_id: "bad" })],
  ["wrong tenant", (r) => ({ ...r, tenant_id: id(99) })],
  ["wrong actor", (r) => ({ ...r, actor_id: id(99) })],
  ["wrong branch", (r) => ({ ...r, branch_id: id(99) })],
  ["wrong register", (r) => ({ ...r, register_id: id(99) })],
  ["total mismatch", (r) => ({ ...r, total_amount: "3.98" })],
  ["numeric money", (r) => ({ ...r, total_amount: 3.99 })],
  ["negative cost", (r) => ({ ...r, physical_cost_amount: "-1.00" })],
  ["extra top-level field", (r) => ({ ...r, bank_response: true })],
  ["wrong receipt version", (r) => ({ ...r, receipt: { ...r.receipt, schema_version: 2 } })],
  [
    "wrong receipt kind",
    (r) => ({ ...r, receipt: { ...r.receipt, document_kind: "physical_cash_refund_receipt" } }),
  ],
  ["test receipt", (r) => ({ ...r, receipt: { ...r.receipt, is_test: true } })],
  ["receipt parent", (r) => ({ ...r, receipt: { ...r.receipt, parent_sale_id: id(99) } })],
  [
    "original receipt number",
    (r) => ({ ...r, receipt: { ...r.receipt, parent_receipt_number: "S-other" } }),
  ],
  ["receipt total", (r) => ({ ...r, receipt: { ...r.receipt, total_amount: "3.98" } })],
  [
    "receipt number exceeds Unicode limit",
    (r) => ({ ...r, receipt: { ...r.receipt, receipt_number: "😀".repeat(101) } }),
  ],
  [
    "line name exceeds Unicode limit",
    (r) => {
      r.receipt.lines[0].name = "😀".repeat(501);
      return r;
    },
  ],
  ["empty lines", (r) => ({ ...r, receipt: { ...r.receipt, lines: [] } })],
  [
    "duplicate lines",
    (r) => ({ ...r, receipt: { ...r.receipt, lines: [r.receipt.lines[0], r.receipt.lines[0]] } }),
  ],
  [
    "wrong original line",
    (r) => ({
      ...r,
      receipt: { ...r.receipt, lines: [{ ...r.receipt.lines[0], original_line_id: id(99) }] },
    }),
  ],
  [
    "wrong position",
    (r) => ({ ...r, receipt: { ...r.receipt, lines: [{ ...r.receipt.lines[0], position: 2 }] } }),
  ],
  [
    "wrong allocation offset",
    (r) => ({ ...r, receipt: { ...r.receipt, lines: [{ ...r.receipt.lines[0], offset: 1 }] } }),
  ],
  [
    "wrong selected quantity",
    (r) => ({
      ...r,
      receipt: { ...r.receipt, lines: [{ ...r.receipt.lines[0], quantity: 3, base_units: 3 }] },
    }),
  ],
  [
    "line imbalance",
    (r) => ({
      ...r,
      receipt: { ...r.receipt, lines: [{ ...r.receipt.lines[0], net_amount: "3.98" }] },
    }),
  ],
  [
    "wrong physical units",
    (r) => ({ ...r, receipt: { ...r.receipt, lines: [{ ...r.receipt.lines[0], base_units: 3 }] } }),
  ],
  [
    "physical units violate dispense step",
    (r) => {
      r.receipt.lines[0].packaging = {
        version: 2,
        unit: "tablet",
        units_per_pack: 30,
        units_per_blister: 10,
        dispense_step: 3,
      };
      return r;
    },
  ],
  [
    "missing payment",
    (r) => ({ ...r, receipt: { ...r.receipt, payments: r.receipt.payments.slice(1) } }),
  ],
  [
    "duplicate new payment UUID",
    (r) => {
      r.receipt.payments[1].id = r.receipt.payments[0].id;
      return r;
    },
  ],
  [
    "duplicate original payment UUID",
    (r) => {
      r.receipt.payments[1].original_payment_id = id(7);
      return r;
    },
  ],
  [
    "unknown original payment",
    (r) => {
      r.receipt.payments[1].original_payment_id = id(99);
      return r;
    },
  ],
  [
    "wrong method",
    (r) => {
      r.receipt.payments[1].method = "qr";
      return r;
    },
  ],
  [
    "payment imbalance",
    (r) => {
      r.receipt.payments[1].amount = "1.01";
      return r;
    },
  ],
  [
    "noncanonical money",
    (r) => {
      r.receipt.payments[1].amount = "01.00";
      return r;
    },
  ],
  [
    "cash evidence",
    (r) => {
      r.receipt.payments[0].terminal_id = "T-1";
      return r;
    },
  ],
  [
    "missing electronic evidence",
    (r) => {
      r.receipt.payments[1].terminal_id = null;
      return r;
    },
  ],
  [
    "different document",
    (r) => {
      r.receipt.payments[1].document_number = "other";
      return r;
    },
  ],
  [
    "noncanonical evidence",
    (r) => {
      r.receipt.payments[1].terminal_id = " T-1 ";
      return r;
    },
  ],
  [
    "early completion by a microsecond",
    (r) => {
      r.receipt.completed_at = "2026-10-20T06:00:00.000003Z";
      return r;
    },
  ],
  [
    "invalid timezone",
    (r) => {
      r.receipt.completed_at = "2026-10-20T06:00:00+99:99";
      return r;
    },
  ],
];

beforeEach(() => {
  vi.clearAllMocks();
  session.active = true;
});
describe("electronic refund posting client", () => {
  it("posts only the chosen shift/reason/comment and preserves cash/card/qr components", async () => {
    vi.mocked(api.post).mockResolvedValue({ data: posted() });
    expect(await postMixedElectronicRefund(scope(), request(), evidence(), details())).toEqual(
      posted(),
    );
    expect(api.post).toHaveBeenCalledWith(`${path}/post`, details(), config);
    expect(api.get).not.toHaveBeenCalled();
  });
  it("recovers the saved historical shift through GET without another POST", async () => {
    const result = { ...posted(), shift_id: id(30) };
    vi.mocked(api.get).mockResolvedValue({ data: result });
    expect(await readMixedElectronicRefundPosting(scope(), request(), evidence())).toEqual(result);
    expect(api.get).toHaveBeenCalledWith(`${path}/posting`, config);
    expect(api.post).not.toHaveBeenCalled();
  });
  it("compares equivalent completion instants with timezone offsets", async () => {
    const result = posted();
    result.receipt.completed_at = "2026-10-20T09:00:00.000005+03:00";
    vi.mocked(api.get).mockResolvedValue({ data: result });
    expect(await readMixedElectronicRefundPosting(scope(), request(), evidence())).toEqual(result);
  });
  it("checks maximum supported money exactly without floating-point rounding", async () => {
    const total = "999999999999.99",
      r = request(),
      e = evidence(),
      result = posted();
    r.request.expected_total_amount = total;
    e.activation.review.request = r.request;
    e.activation.review.preview.total_amount = total;
    e.activation.review.preview.lines[0].gross_amount = total;
    e.activation.review.preview.lines[0].discount_amount = "0.00";
    e.activation.review.preview.lines[0].net_amount = total;
    e.activation.review.preview.payments = [
      { original_payment_id: id(8), method: "card", amount: total, currency: "TJS" },
    ];
    e.confirmations = [{ ...e.confirmations[0], amount: total }];
    result.total_amount = total;
    result.receipt.total_amount = total;
    result.receipt.lines[0].gross_amount = total;
    result.receipt.lines[0].discount_amount = "0.00";
    result.receipt.lines[0].net_amount = total;
    result.receipt.payments = [{ ...result.receipt.payments[1], amount: total }];
    vi.mocked(api.get).mockResolvedValue({ data: result });
    expect(await readMixedElectronicRefundPosting(scope(), r, e)).toEqual(result);
  });
  it("accepts historical text within backend Unicode code-point limits", async () => {
    const result = posted();
    result.receipt.lines[0].name = "😀".repeat(300);
    result.receipt.receipt_number = "😀".repeat(60);
    vi.mocked(api.get).mockResolvedValue({ data: result });
    expect(await readMixedElectronicRefundPosting(scope(), request(), evidence())).toEqual(result);
  });
  it.each(malformed)("rejects malformed posted result: %s", async (_name, mutate) => {
    vi.mocked(api.get).mockResolvedValue({ data: mutate(posted()) });
    await expect(
      readMixedElectronicRefundPosting(scope(), request(), evidence()),
    ).rejects.toThrow();
    expect(api.get).toHaveBeenCalledTimes(1);
    expect(api.post).not.toHaveBeenCalled();
  });
  it("rejects a different POST shift", async () => {
    vi.mocked(api.post).mockResolvedValue({ data: { ...posted(), shift_id: id(30) } });
    await expect(
      postMixedElectronicRefund(scope(), request(), evidence(), details()),
    ).rejects.toThrow();
  });
  it.each(operations)(
    "rejects incomplete or conflicting confirmation input before %s",
    async (_name, operation) => {
      const partial = evidence();
      partial.state = "requires_reconciliation";
      partial.confirmations = partial.confirmations.slice(0, 1);
      partial.missing_payment_ids = [id(11)];
      await expect(operation(scope(), request(), partial)).rejects.toThrow();
      const wrong = evidence();
      wrong.confirmations[0].document_number = " D-1 ";
      await expect(operation(scope(), request(), wrong)).rejects.toThrow();
      expect(api.post).not.toHaveBeenCalled();
      expect(api.get).not.toHaveBeenCalled();
    },
  );
  it.each([
    ["invalid shift", { ...details(), shift_id: "../other" }],
    ["unknown reason", { ...details(), reason_code: "invalid" }],
    ["long comment", { ...details(), comment: "x".repeat(501) }],
    ["caller amount", { ...details(), total_amount: "3.99" }],
    ["caller evidence", { ...details(), terminal_id: "T-1" }],
    ["absent details", undefined],
  ])("rejects invalid POST details without falling back to GET: %s", async (_name, payload) => {
    await expect(
      postMixedElectronicRefund(
        scope(),
        request(),
        evidence(),
        payload as ReturnType<typeof details>,
      ),
    ).rejects.toThrow();
    expect(api.post).not.toHaveBeenCalled();
    expect(api.get).not.toHaveBeenCalled();
  });
  it.each(operations)(
    "validates identifiers and nested review before %s",
    async (_name, operation) => {
      await expect(
        operation({ ...scope(), parentSaleId: "../other" }, request(), evidence()),
      ).rejects.toThrow();
      const wrong = evidence();
      wrong.activation.review.preview.total_amount = "9.99";
      await expect(operation(scope(), request(), wrong)).rejects.toThrow();
      expect(api.post).not.toHaveBeenCalled();
      expect(api.get).not.toHaveBeenCalled();
    },
  );
  it.each(operations)("blocks an inactive session before %s", async (_name, operation) => {
    session.active = false;
    await expect(operation(scope(), request(), evidence())).rejects.toThrow();
    expect(api.post).not.toHaveBeenCalled();
    expect(api.get).not.toHaveBeenCalled();
  });
  it.each(operations)("discards a %s response after session change", async (_name, operation) => {
    const respond = async () => {
      session.active = false;
      return { data: posted() };
    };
    vi.mocked(api.post).mockImplementation(respond);
    vi.mocked(api.get).mockImplementation(respond);
    await expect(operation(scope(), request(), evidence())).rejects.toThrow();
  });
  it.each(operations)("captures authority before %s dispatch", async (_name, operation) => {
    vi.mocked(api.post).mockResolvedValue({ data: posted() });
    vi.mocked(api.get).mockResolvedValue({ data: posted() });
    await operation(scope(), request(), evidence());
    expect(withAuthSession).toHaveBeenCalledWith({ timeout: 8_000 });
    const dispatch =
      vi.mocked(api.post).mock.invocationCallOrder[0] ??
      vi.mocked(api.get).mock.invocationCallOrder[0];
    expect(vi.mocked(withAuthSession).mock.invocationCallOrder[0]).toBeLessThan(dispatch);
  });
  it.each(operations)("uses input snapshots during %s", async (_name, operation) => {
    const s = scope(),
      r = request(),
      e = evidence();
    const respond = async () => {
      s.tenantId = id(99);
      s.parentSaleId = id(99);
      r.request.expected_revision_hash = "b".repeat(64);
      e.activation.review.preview.payments[0].amount = "9.00";
      e.confirmations[0].document_number = "changed";
      return { data: posted() };
    };
    vi.mocked(api.post).mockImplementation(respond);
    vi.mocked(api.get).mockImplementation(respond);
    expect(await operation(s, r, e)).toEqual(posted());
  });
  it("snapshots the selected POST shift", async () => {
    const input = details();
    vi.mocked(api.post).mockImplementation(async () => {
      input.shift_id = id(99);
      return { data: posted() };
    });
    expect(await postMixedElectronicRefund(scope(), request(), evidence(), input)).toEqual(
      posted(),
    );
  });
  it.each(["conflict", "lost response"])(
    "propagates %s without retry and allows explicit GET recovery",
    async (message) => {
      const error = new Error(message);
      vi.mocked(api.post).mockRejectedValue(error);
      await expect(
        postMixedElectronicRefund(scope(), request(), evidence(), details()),
      ).rejects.toBe(error);
      expect(api.post).toHaveBeenCalledTimes(1);
      vi.mocked(api.get).mockResolvedValue({ data: posted() });
      expect(await readMixedElectronicRefundPosting(scope(), request(), evidence())).toEqual(
        posted(),
      );
      expect(api.post).toHaveBeenCalledTimes(1);
    },
  );
});

describe("RAM-only command context checks", () => {
  it.each(operations)(
    "accepts and checks a callback without sending it during %s",
    async (_name, operation) => {
      const contextCheck = vi.fn();
      const commandScope = { ...scope(), contextCheck };
      vi.mocked(api.post).mockResolvedValue({ data: posted() });
      vi.mocked(api.get).mockResolvedValue({ data: posted() });
      await expect(operation(commandScope, request(), evidence())).resolves.toEqual(posted());
      expect(contextCheck.mock.calls.length).toBeGreaterThanOrEqual(2);
      for (const call of [...vi.mocked(api.post).mock.calls, ...vi.mocked(api.get).mock.calls]) {
        for (const argument of call) {
          if (argument && typeof argument === "object") {
            expect(argument).not.toHaveProperty("contextCheck");
          }
        }
      }
    },
  );

  it.each(operations)(
    "rejects a non-callable callback and unknown scope fields before %s",
    async (_name, operation) => {
      const invalidCallback = { ...scope(), contextCheck: "not-callable" as unknown as () => void };
      const unknownField = { ...scope(), unexpected: true };
      await expect(operation(invalidCallback, request(), evidence())).rejects.toThrow();
      await expect(operation(unknownField, request(), evidence())).rejects.toThrow();
      expect(api.post).not.toHaveBeenCalled();
      expect(api.get).not.toHaveBeenCalled();
      expect(getCurrentShift).not.toHaveBeenCalled();
    },
  );

  it.each(operations)("blocks changed command context before %s", async (_name, operation) => {
    const commandScope = {
      ...scope(),
      contextCheck: () => {
        throw new Error("Command context changed");
      },
    };
    await expect(operation(commandScope, request(), evidence())).rejects.toThrow(
      "Command context changed",
    );
    expect(api.post).not.toHaveBeenCalled();
    expect(api.get).not.toHaveBeenCalled();
    expect(getCurrentShift).not.toHaveBeenCalled();
  });

  it("blocks POST when command context changes during the authority await", async () => {
    let active = true;
    const commandScope = {
      ...scope(),
      contextCheck: () => {
        if (!active) throw new Error("Command context changed");
      },
    };
    vi.mocked(getCurrentShift).mockImplementationOnce(async () => {
      active = false;
      return null;
    });
    await expect(
      postMixedElectronicRefund(commandScope, request(), evidence(), details()),
    ).rejects.toThrow("Command context changed");
    expect(getCurrentShift).toHaveBeenCalledTimes(1);
    expect(api.post).not.toHaveBeenCalled();
    expect(api.get).not.toHaveBeenCalled();
  });

  it.each(operations)(
    "rejects %s results after command context changes",
    async (_name, operation) => {
      let active = true;
      const contextCheck = vi.fn(() => {
        if (!active) throw new Error("Command context changed");
      });
      const commandScope = { ...scope(), contextCheck };
      const changeContext = async () => {
        active = false;
        return { data: posted() };
      };
      vi.mocked(api.post).mockImplementation(changeContext);
      vi.mocked(api.get).mockImplementation(changeContext);
      await expect(operation(commandScope, request(), evidence())).rejects.toThrow(
        "Command context changed",
      );
      expect(contextCheck.mock.calls.length).toBeGreaterThanOrEqual(2);
      expect(vi.mocked(api.post).mock.calls.length + vi.mocked(api.get).mock.calls.length).toBe(1);
    },
  );
});
