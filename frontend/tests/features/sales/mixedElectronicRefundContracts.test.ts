import { beforeEach, describe, expect, it, vi } from "vitest";
import { webcrypto } from "node:crypto";
import type { ReconciledMixedElectronicRefund } from "@/features/sales/mixedElectronicRefundConfirmations";
import type { PostedMixedElectronicRefund } from "@/features/sales/mixedElectronicRefundPosting";
import {
  postMixedElectronicRefund,
  readMixedElectronicRefundPosting,
} from "@/features/sales/mixedElectronicRefundPosting";
import {
  quoteMixedElectronicRefundReview,
  saveMixedElectronicRefundReview,
  readMixedElectronicRefundReview,
  mixedElectronicRefundSelectionSchema,
  mixedElectronicRefundPreviewSchema,
} from "@/features/sales/mixedElectronicRefundReview";
import { activateMixedElectronicRefundReview } from "@/features/sales/mixedElectronicRefundActivation";
import { recordMixedElectronicRefundConfirmation } from "@/features/sales/mixedElectronicRefundConfirmations";
import { api } from "@/lib/api";
const state = vi.hoisted(() => ({
  active: true,
  refund: true,
  external: true,
  readonly: false,
  shift: vi.fn(),
}));
vi.mock("@/features/pos/posStorageScope", () => ({
  capturePosSessionGuard: () => () => state.active,
}));
vi.mock("@/features/auth/logoutState", () => ({ getAuthEpoch: () => "fixed" }));
vi.mock("@/features/auth/permissions", () => ({
  hasPermissionForBranch: (_u: unknown, p: string) =>
    p === "pos.refund" ? state.refund : state.external,
}));
vi.mock("@/stores/auth", () => ({
  useAuthStore: {
    getState: () => ({
      accessToken: "synthetic",
      user: { support_access: { is_read_only: state.readonly } },
    }),
  },
}));
vi.mock("@/features/pos/api", () => ({ getCurrentShift: state.shift }));
vi.mock("@/lib/api", () => ({
  api: { get: vi.fn(), post: vi.fn() },
  withAuthSession: (c: object) => c,
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

function mixed() {
  const e = evidence(),
    p = posted(),
    r = request();
  const q = e.activation.review.preview;
  q.lines[0]!.gross_amount = "3.00";
  q.lines[0]!.net_amount = "2.99";
  q.lines.unshift({
    inventory_kind: "legacy_pack",
    line_id: id(30),
    quantity: "0.500",
    offset: "0.125",
    gross_amount: "1.00",
    discount_amount: "0.00",
    net_amount: "1.00",
  });
  const physical = p.receipt.lines[0]!;
  physical.gross_amount = "3.00";
  physical.net_amount = "2.99";
  physical.position = 2;
  physical.original_position = 2;
  p.receipt.lines.unshift({
    inventory_kind: "legacy_pack",
    original_line_id: id(30),
    position: 1,
    original_position: 1,
    name: "Legacy",
    original_retail_unit_price: "2.00",
    quantity: "0.500",
    offset: "0.125",
    gross_amount: "1.00",
    discount_amount: "0.00",
    net_amount: "1.00",
  });
  const selection = {
    items: q.lines.map((l) =>
      l.inventory_kind === "legacy_pack"
        ? { inventory_kind: l.inventory_kind, line_id: l.line_id, quantity: l.quantity }
        : {
            inventory_kind: l.inventory_kind,
            line_id: l.line_id,
            quantity: l.quantity,
            sale_format: l.sale_format,
          },
    ),
  };
  const req = { ...r, request: { ...r.request, selection } };
  e.activation.review.request = req.request;
  return { e, p, req, preview: q };
}
describe("mixed historical contracts and actual authority guards", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    state.active = true;
    state.refund = true;
    state.external = true;
    state.readonly = false;
    state.shift.mockResolvedValue({
      id: id(12),
      status: "open",
      opened_by_user_id: id(2),
      branch_id: id(4),
      register_id: id(9),
      currency: "TJS",
    });
    Object.defineProperty(crypto, "subtle", { configurable: true, value: webcrypto.subtle });
  });
  it("quotes, saves, reads and posts legacy+physical cash/card/QR exact history", async () => {
    const { e, p, req, preview } = mixed();
    vi.mocked(api.post)
      .mockResolvedValueOnce({ data: preview })
      .mockResolvedValueOnce({ data: e.activation.review })
      .mockResolvedValueOnce({ data: p });
    vi.mocked(api.get)
      .mockResolvedValueOnce({ data: e.activation.review })
      .mockResolvedValueOnce({ data: p });
    expect(await quoteMixedElectronicRefundReview(scope(), req.request.selection)).toEqual(preview);
    expect(await saveMixedElectronicRefundReview(scope(), req)).toEqual(e.activation.review);
    expect(await readMixedElectronicRefundReview(scope(), req)).toEqual(e.activation.review);
    expect(await postMixedElectronicRefund(scope(), req, e, details())).toEqual(p);
    state.readonly = true;
    state.shift.mockResolvedValue(null);
    expect(await readMixedElectronicRefundPosting(scope(), req, e)).toEqual(p);
    for (const [url] of vi.mocked(api.post).mock.calls)
      expect(url).toContain("/mixed-electronic-refunds/");
  });
  it.each(["0.50", "5e-1", 0.5, "-0.500", "0.000"])("rejects malformed legacy quantity %s", (v) => {
    const x = mixed().req.request.selection;
    const bad = { ...x, items: [{ ...x.items[0], quantity: v }] };
    expect(mixedElectronicRefundSelectionSchema.safeParse(bad).success).toBe(false);
  });
  it("rejects wrong union fields and duplicate identity across row kinds", () => {
    const x = mixed().req.request.selection;
    expect(
      mixedElectronicRefundSelectionSchema.safeParse({
        items: [{ ...x.items[0], sale_format: "unit" }],
      }).success,
    ).toBe(false);
    expect(
      mixedElectronicRefundSelectionSchema.safeParse({
        items: [x.items[0], { ...x.items[1], line_id: x.items[0]!.line_id }],
      }).success,
    ).toBe(false);
  });
  it.each(["0.01", "4.00", "999999999999.99"])("rejects altered totals %s", (total) => {
    const q = mixed().preview;
    q.total_amount = total;
    expect(mixedElectronicRefundPreviewSchema.safeParse(q).success).toBe(false);
  });
  it.each(["cost", "offset", "order", "kind", "payment"])(
    "rejects corrupted mixed posted %s",
    async (kind) => {
      const { e, p, req } = mixed();
      const bad: Record<string, unknown> = { ...p };
      if (kind === "cost") {
        delete bad.physical_cost_amount;
        bad.total_cost_amount = "1.25";
      }
      if (kind === "offset") p.receipt.lines[0]!.offset = "0.126";
      if (kind === "order") p.receipt.lines.reverse();
      if (kind === "kind")
        bad.receipt = { ...p.receipt, document_kind: "mixed_cash_refund_receipt" };
      if (kind === "payment") p.receipt.payments.reverse();
      vi.mocked(api.get).mockResolvedValue({ data: bad });
      await expect(readMixedElectronicRefundPosting(scope(), req, e)).rejects.toThrow();
    },
  );
  it.each(["refund", "readonly", "shift", "context"])(
    "blocks command on %s change before HTTP",
    async (kind) => {
      const { e, req } = mixed();
      if (kind === "refund") state.refund = false;
      if (kind === "readonly") state.readonly = true;
      if (kind === "shift") state.shift.mockResolvedValue(null);
      let active = true;
      const s = {
        ...scope(),
        contextCheck: () => {
          if (!active) throw new Error("changed");
        },
      };
      if (kind === "context")
        state.shift.mockImplementation(async () => {
          active = false;
          return { id: id(12) };
        });
      await expect(postMixedElectronicRefund(s, req, e, details())).rejects.toThrow();
      expect(api.post).not.toHaveBeenCalled();
    },
  );
  it("requires both permissions for evidence without bypassing server MFA", async () => {
    const { e, req } = mixed();
    state.external = false;
    await expect(
      recordMixedElectronicRefundConfirmation(scope(), req, e.activation, {
        original_payment_id: id(8),
        terminal_id: "T-1",
        document_number: "D-1",
      }),
    ).rejects.toThrow();
    expect(api.post).not.toHaveBeenCalled();
  });
  it("rejects a changed session during shift lookup", async () => {
    const { e, req } = mixed();
    state.shift.mockImplementation(async () => {
      state.active = false;
      return { id: id(12) };
    });
    await expect(postMixedElectronicRefund(scope(), req, e, details())).rejects.toThrow();
    expect(api.post).not.toHaveBeenCalled();
  });
  it("matches activation shift and full historical review", async () => {
    const { e, req } = mixed();
    vi.mocked(api.post).mockResolvedValue({ data: e.activation });
    await expect(activateMixedElectronicRefundReview(scope(), req, id(12))).rejects.toThrow();
  });
});
