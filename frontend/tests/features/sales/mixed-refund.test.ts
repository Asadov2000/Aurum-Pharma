import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  getMixedRefundOptions,
  quoteMixedRefund,
  parseMixedRefundStatus,
  mixedRefundRequestSchema,
  mixedQuantity,
  refundCents,
  validateMixedRefundQuote,
  getMixedRefundStatus,
  type MixedRefundOptions,
  type MixedRefundRequest,
  type MixedRefundQuote,
} from "@/features/sales/mixedRefund";

const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), generation: 0 }));
vi.mock("@/lib/api", () => ({ api: { get: mocks.get, post: mocks.post } }));
vi.mock("@/features/pos/posStorageScope", () => ({
  capturePosSessionGuard: () => {
    const generation = mocks.generation;
    return () => generation === mocks.generation;
  },
}));
const uuid = (value: number): string =>
  `20000000-0000-4000-8000-${String(value).padStart(12, "0")}`;
const scope = { tenantId: uuid(1), userId: uuid(2), branchId: uuid(3), registerId: uuid(4) };
const parentId = uuid(5);
const options: MixedRefundOptions = {
  parent_sale_id: parentId,
  branch_id: scope.branchId,
  register_id: scope.registerId,
  original_receipt_number: "R-1",
  reason_mode: "required",
  lines: [
    {
      inventory_kind: "legacy_pack",
      line_id: uuid(6),
      name: "Legacy",
      unit_label: "уп.",
      sold_quantity: "1.000",
      returned_quantity: "0.000",
      remaining_quantity: "1.000",
      original_retail_unit_price: "3.00",
    },
    {
      inventory_kind: "physical_unit",
      line_id: uuid(7),
      name: "Physical",
      unit_label: "табл.",
      packaging: {
        version: 2,
        unit: "tablet",
        units_per_pack: 30,
        units_per_blister: 10,
        dispense_step: 1,
      },
      sold_base_units: 30,
      returned_base_units: 0,
      remaining_base_units: 30,
      original_sale_format: "pack",
      original_retail_unit_price: "60.00",
    },
  ],
};
const request: MixedRefundRequest = {
  operation_id: uuid(8),
  shift_id: uuid(9),
  expected_total_amount: "5.50",
  expected_revision_hash: "a".repeat(64),
  reason_code: "quality_issue",
  comment: null,
  items: [
    { inventory_kind: "legacy_pack", line_id: uuid(6), quantity: "0.500" },
    { inventory_kind: "physical_unit", line_id: uuid(7), quantity: 2, sale_format: "unit" },
  ],
};
const quote: MixedRefundQuote = {
  parent_sale_id: parentId,
  revision_hash: request.expected_revision_hash,
  total_amount: "5.50",
  currency: "TJS",
  lines: [
    {
      ...request.items[0]!,
      inventory_kind: "legacy_pack",
      quantity: "0.500",
      offset: "0.000",
      gross_amount: "1.50",
      discount_amount: "0.00",
      net_amount: "1.50",
    },
    {
      ...request.items[1]!,
      inventory_kind: "physical_unit",
      quantity: 2,
      sale_format: "unit",
      offset: 0,
      base_units: 2,
      gross_amount: "4.00",
      discount_amount: "0.00",
      net_amount: "4.00",
    },
  ],
};
function completed() {
  return {
    state: "completed",
    operation_id: request.operation_id,
    parent_sale_id: parentId,
    sale_id: uuid(10),
    total_amount: "5.50",
    receipt: {
      schema_version: 4,
      document_kind: "mixed_cash_refund_receipt",
      parent_sale_id: parentId,
      parent_receipt_number: "R-1",
      receipt_number: "R-2",
      receipt_sequence: 2,
      completed_at: "2026-10-12T05:00:00Z",
      is_test: false,
      payment_method: "cash",
      currency: "TJS",
      total_amount: "5.50",
      lines: [
        {
          inventory_kind: "legacy_pack",
          original_line_id: uuid(6),
          position: 1,
          original_position: 1,
          name: "Legacy",
          original_retail_unit_price: "3.00",
          quantity: "0.500",
          offset: "0.000",
          gross_amount: "1.50",
          discount_amount: "0.00",
          net_amount: "1.50",
        },
        {
          inventory_kind: "physical_unit",
          original_line_id: uuid(7),
          position: 2,
          original_position: 2,
          name: "Physical",
          original_retail_unit_price: "60.00",
          packaging:
            options.lines[1]!.inventory_kind === "physical_unit"
              ? options.lines[1]!.packaging
              : undefined,
          original_sale_format: "pack",
          sale_format: "unit",
          quantity: 2,
          base_units: 2,
          offset: 0,
          gross_amount: "4.00",
          discount_amount: "0.00",
          net_amount: "4.00",
        },
      ],
    },
  };
}
function returnedOptions(): MixedRefundOptions {
  const value = structuredClone(options);
  const legacy = value.lines[0]!;
  const physical = value.lines[1]!;
  if (legacy.inventory_kind === "legacy_pack") {
    legacy.returned_quantity = "0.500";
    legacy.remaining_quantity = "0.500";
  }
  if (physical.inventory_kind === "physical_unit") {
    physical.returned_base_units = 2;
    physical.remaining_base_units = 28;
  }
  return value;
}
describe("strict mixed refund client", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.generation = 0;
    mocks.get.mockResolvedValue({ data: options });
    mocks.post.mockResolvedValue({ data: quote });
  });
  it("uses exact bigint cents and thousandths at maximum precision", () => {
    expect(mixedQuantity("99999999999.999")).toBe(99999999999999n);
    expect(refundCents("999999999999.99")).toBe(99999999999999n);
    expect(() => mixedQuantity("0.0001")).toThrow();
  });
  it("reads scoped original options and quotes a discriminated selection", async () => {
    expect(await getMixedRefundOptions(parentId, scope.branchId, scope.registerId, scope)).toEqual(
      options,
    );
    expect(await quoteMixedRefund(parentId, request.items, scope)).toEqual(quote);
    expect(mocks.post).toHaveBeenCalledWith(
      `/sales/${parentId}/mixed-cash-refunds/quote`,
      { items: request.items },
      { timeout: 8_000 },
    );
    validateMixedRefundQuote(quote, request.items, options);
  });
  it.each(["parent", "branch", "register", "cursor", "extra"])(
    "rejects wrong options %s",
    async (field) => {
      const value = structuredClone(options);
      if (field === "parent") value.parent_sale_id = uuid(99);
      if (field === "branch") value.branch_id = uuid(99);
      if (field === "register") value.register_id = uuid(99);
      if (field === "cursor" && value.lines[0]!.inventory_kind === "legacy_pack")
        value.lines[0]!.remaining_quantity = "0.999";
      mocks.get.mockResolvedValue({ data: field === "extra" ? { ...value, cost: "1.00" } : value });
      await expect(
        getMixedRefundOptions(parentId, scope.branchId, scope.registerId, scope),
      ).rejects.toThrow();
    },
  );
  it.each(["parent", "kind", "quantity", "sum", "duplicate"])(
    "rejects quote %s tampering",
    async (field) => {
      const value = structuredClone(quote);
      if (field === "parent") value.parent_sale_id = uuid(99);
      if (field === "kind") value.lines[0] = { ...value.lines[1]!, line_id: uuid(6) };
      if (field === "quantity" && value.lines[0]!.inventory_kind === "legacy_pack")
        value.lines[0]!.quantity = "0.501";
      if (field === "sum") value.total_amount = "5.49";
      if (field === "duplicate") value.lines[1]!.line_id = uuid(6);
      mocks.post.mockResolvedValue({ data: value });
      await expect(quoteMixedRefund(parentId, request.items, scope)).rejects.toThrow();
    },
  );
  it("validates historical quantity conversion, cursors and original order before review", () => {
    const stale = structuredClone(quote);
    if (stale.lines[0]!.inventory_kind === "legacy_pack") stale.lines[0]!.offset = "0.001";
    expect(() => validateMixedRefundQuote(stale, request.items, options)).toThrow();
    const units = structuredClone(quote);
    if (units.lines[1]!.inventory_kind === "physical_unit") units.lines[1]!.base_units = 3;
    expect(() => validateMixedRefundQuote(units, request.items, options)).toThrow();
    expect(() =>
      validateMixedRefundQuote(
        { ...quote, lines: [...quote.lines].reverse() },
        request.items,
        options,
      ),
    ).toThrow();
  });
  it.each([0.5, "0.000", "0.0001", "5e-1", "-0.500"])(
    "refuses invalid legacy request quantity %s",
    (quantity) => {
      expect(() =>
        mixedRefundRequestSchema.parse({
          ...request,
          items: [{ inventory_kind: "legacy_pack", line_id: uuid(6), quantity }],
        }),
      ).toThrow();
    },
  );
  it("refuses zero payout and duplicate IDs across inventory kinds", () => {
    expect(() =>
      mixedRefundRequestSchema.parse({ ...request, expected_total_amount: "0.00" }),
    ).toThrow();
    expect(() =>
      mixedRefundRequestSchema.parse({
        ...request,
        items: [request.items[0], { ...request.items[1], line_id: uuid(6) }],
      }),
    ).toThrow();
  });
  it("rejects a changed session while GET or quote is in flight", async () => {
    mocks.get.mockImplementationOnce(() => {
      mocks.generation++;
      return Promise.resolve({ data: options });
    });
    await expect(
      getMixedRefundOptions(parentId, scope.branchId, scope.registerId, scope),
    ).rejects.toThrow("Аккаунт изменился");
    mocks.post.mockImplementationOnce(() => {
      mocks.generation++;
      return Promise.resolve({ data: quote });
    });
    await expect(quoteMixedRefund(parentId, request.items, scope)).rejects.toThrow(
      "Аккаунт изменился",
    );
  });
  it("binds completed receipt to historical source identities and current returned cursors", async () => {
    const result = completed();
    expect(parseMixedRefundStatus(result, parentId, request, returnedOptions())).toEqual(result);
    mocks.get
      .mockResolvedValueOnce({ data: result })
      .mockResolvedValueOnce({ data: returnedOptions() });
    expect(await getMixedRefundStatus(parentId, request, scope)).toEqual(result);
    expect(mocks.post).not.toHaveBeenCalled();
    expect(() => parseMixedRefundStatus(result, parentId, request, options)).toThrow();
  });
  it.each(["order", "price", "name", "receipt", "test", "kind", "cost", "sum"])(
    "rejects corrupt completed %s",
    (field) => {
      const value = completed();
      if (field === "order") value.receipt.lines[1]!.original_position = 1;
      if (field === "price") value.receipt.lines[0]!.original_retail_unit_price = "3.01";
      if (field === "name") value.receipt.lines[0]!.name = "Changed";
      if (field === "receipt") value.receipt.parent_receipt_number = "R-99";
      if (field === "test") value.receipt.is_test = true;
      if (field === "kind") value.receipt.document_kind = "mixed_sale_receipt";
      if (field === "sum") value.receipt.total_amount = "5.49";
      expect(() =>
        parseMixedRefundStatus(
          field === "cost" ? { ...value, physical_cost_amount: "1.00" } : value,
          parentId,
          request,
          returnedOptions(),
        ),
      ).toThrow();
    },
  );
});
