import { webcrypto } from "node:crypto";
import { TextEncoder } from "node:util";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  completePhysicalDisposition,
  getPhysicalDispositionStatus,
  listPhysicalDispositionHistory,
  physicalDispositionInputSchema,
  physicalDispositionQuantity,
  physicalDispositionRequestHash,
  physicalDispositionTarget,
  validatePhysicalDispositionResult,
  type PhysicalDispositionRequest,
  type PhysicalDispositionReviewedTarget,
} from "@/features/customerReturns/physicalDisposition";

const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), generation: 0 }));
vi.mock("@/lib/api", () => ({ api: { get: mocks.get, post: mocks.post } }));
vi.mock("@/features/pos/posStorageScope", () => ({
  capturePosSessionGuard: () => {
    const generation = mocks.generation;
    return () => generation === mocks.generation;
  },
}));
const uuid = (value: number): string =>
  `00000000-0000-0000-0000-${String(value).padStart(12, "0")}`;
const scope = { tenantId: uuid(1), userId: uuid(9), branchId: uuid(8) };
const reviewed: PhysicalDispositionReviewedTarget = {
  inventory_kind: "physical_unit",
  intake_operation_id: uuid(2),
  line_id: uuid(3),
  lot_id: uuid(4),
  base_units: 5,
  unit_label: "табл.",
};
const request: PhysicalDispositionRequest = {
  decision_operation_id: uuid(5),
  target: physicalDispositionTarget(reviewed),
  disposition_type: "disposed",
  reason_code: "damaged",
  comment: "Претензия",
};
async function resolved(
  target: PhysicalDispositionReviewedTarget = reviewed,
  original: PhysicalDispositionRequest = request,
) {
  return {
    version: 1 as const,
    document_kind: "physical_customer_return_disposition" as const,
    state: "resolved" as const,
    decision_operation_id: original.decision_operation_id,
    tenant_id: scope.tenantId,
    branch_id: scope.branchId,
    parent_sale_id: uuid(10),
    intake_kind: "zero_return" as const,
    target,
    disposition_type: original.disposition_type,
    reason_code: original.reason_code,
    comment: original.comment,
    request_hash: await physicalDispositionRequestHash(original, scope.tenantId),
    actor_id: uuid(11),
    resolved_at: "2026-10-12T13:40:00+05:00",
  };
}
beforeEach(() => {
  vi.clearAllMocks();
  mocks.generation = 0;
  vi.stubGlobal("crypto", webcrypto);
  vi.stubGlobal("TextEncoder", TextEncoder);
});
describe("physical disposition canonical contract", () => {
  it("matches the independent ADR/backend canonical SHA256 vector", async () => {
    expect(await physicalDispositionRequestHash(request, scope.tenantId)).toBe(
      "cb8b1475f229e73c747521d6e82b6d0716096dd733592095a9546ee2c124ea68",
    );
    expect(
      await physicalDispositionRequestHash(
        { ...request, decision_operation_id: uuid(12) },
        scope.tenantId,
      ),
    ).toBe(await physicalDispositionRequestHash(request, scope.tenantId));
  });
  it("uses Python whitespace and Unicode code point limits without Unicode normalization", () => {
    const input = { ...request, decision_operation_id: undefined };
    delete input.decision_operation_id;
    expect(
      physicalDispositionInputSchema.parse({ ...input, comment: "\u0085\u001cТекст\u001f" })
        .comment,
    ).toBe("Текст");
    expect(
      physicalDispositionInputSchema.parse({ ...input, comment: "\ufeffТекст\ufeff" }).comment,
    ).toBe("\ufeffТекст\ufeff");
    expect(
      physicalDispositionInputSchema.parse({ ...input, comment: " \u0085" }).comment,
    ).toBeNull();
    expect(
      physicalDispositionInputSchema.parse({ ...input, comment: "😀".repeat(2000) }).comment,
    ).toHaveLength(4000);
    expect(
      physicalDispositionInputSchema.safeParse({ ...input, comment: "😀".repeat(2001) }).success,
    ).toBe(false);
  });
  it.each(["\0", "\ud800", "\udfff"])("rejects invalid UTF8/NUL %j", (comment) => {
    const input = {
      target: request.target,
      disposition_type: request.disposition_type,
      reason_code: request.reason_code,
    };
    expect(physicalDispositionInputSchema.safeParse({ ...input, comment }).success).toBe(false);
  });
  it("formats physical quantities without pack conversion", () => {
    expect(physicalDispositionQuantity(reviewed)).toBe("5 табл.");
  });
  it("accepts immutable historical actor on an authorized resolver replay", async () => {
    const data = await resolved();
    expect(await validatePhysicalDispositionResult(data, scope, request, reviewed)).toEqual(data);
    expect(data.actor_id).not.toBe(scope.userId);
  });
  it.each([
    { version: "1" },
    { document_kind: "mixed_customer_return_disposition" },
    { state: "prepared" },
    { tenant_id: uuid(90) },
    { branch_id: uuid(90) },
    { decision_operation_id: uuid(90) },
    { reason_code: "other" },
    { comment: "Претензия " },
    { request_hash: "0".repeat(64) },
    { cost_amount: "1.00" },
    { resolved_at: "2026-10-12T13:40:00" },
  ])("rejects altered result fields %j", async (change) => {
    await expect(
      validatePhysicalDispositionResult(
        { ...(await resolved()), ...change },
        scope,
        request,
        reviewed,
      ),
    ).rejects.toThrow();
  });
  it("rejects changed whole quantity and unexpected inventory/private snapshot fields", async () => {
    const data = await resolved();
    await expect(
      validatePhysicalDispositionResult(
        { ...data, target: { ...reviewed, base_units: 6 } },
        scope,
        request,
        reviewed,
      ),
    ).rejects.toThrow();
    await expect(
      validatePhysicalDispositionResult({ ...data, target: { ...reviewed, cost: "1.00" } }, scope),
    ).rejects.toThrow();
    await expect(
      validatePhysicalDispositionResult(
        { ...data, target: { ...reviewed, inventory_kind: "legacy_pack" } },
        scope,
      ),
    ).rejects.toThrow();
  });
  it("validates physical historical unit and whole safe integer", async () => {
    const physical: PhysicalDispositionReviewedTarget = {
      inventory_kind: "physical_unit",
      intake_operation_id: uuid(2),
      line_id: uuid(3),
      lot_id: uuid(4),
      base_units: 2,
      unit_label: "капс.",
    };
    const physicalRequest = { ...request, target: physicalDispositionTarget(physical) };
    const data = await resolved(physical, physicalRequest);
    expect(await validatePhysicalDispositionResult(data, scope, physicalRequest, physical)).toEqual(
      data,
    );
    for (const change of [
      { unit_label: "уп." },
      { base_units: 0 },
      { base_units: 1.5 },
      { base_units: "2" },
      { base_units: Number.MAX_SAFE_INTEGER + 1 },
    ])
      await expect(
        validatePhysicalDispositionResult({ ...data, target: { ...physical, ...change } }, scope),
      ).rejects.toThrow();
  });
});
describe("physical disposition HTTP boundary", () => {
  it("sends no quantity or extra metadata in the explicit POST", async () => {
    const data = await resolved();
    mocks.post.mockResolvedValue({ data });
    expect(await completePhysicalDisposition(request, reviewed, scope)).toEqual(data);
    expect(mocks.post).toHaveBeenCalledWith("/customer-returns/physical/dispositions", request);
  });
  it("rejects mismatched reviewed identity before any HTTP call", async () => {
    await expect(
      completePhysicalDisposition(request, { ...reviewed, line_id: uuid(90) }, scope),
    ).rejects.toThrow();
    expect(mocks.post).not.toHaveBeenCalled();
  });
  it("status only uses GET and binds not_found branch and operation", async () => {
    const data = {
      state: "not_found",
      decision_operation_id: request.decision_operation_id,
      branch_id: scope.branchId,
    };
    mocks.get.mockResolvedValue({ data });
    expect(await getPhysicalDispositionStatus(request, reviewed, scope)).toEqual(data);
    expect(mocks.get).toHaveBeenCalledWith(
      `/customer-returns/physical/dispositions/operations/${request.decision_operation_id}`,
      { params: { branch_id: scope.branchId } },
    );
    for (const change of [
      { branch_id: uuid(90) },
      { decision_operation_id: uuid(90) },
      { unexpected: true },
    ]) {
      mocks.get.mockResolvedValue({ data: { ...data, ...change } });
      await expect(getPhysicalDispositionStatus(request, reviewed, scope)).rejects.toThrow();
    }
    expect(mocks.post).not.toHaveBeenCalled();
  });
  it("rejects response from a previous account/session", async () => {
    const data = await resolved();
    mocks.post.mockImplementation(async () => {
      mocks.generation += 1;
      return { data };
    });
    await expect(completePhysicalDisposition(request, reviewed, scope)).rejects.toThrow(
      "Аккаунт изменился",
    );
  });
  it("validates history hashes, tenant, filter and pagination", async () => {
    const item = await resolved();
    const data = {
      items: [item],
      total: 1,
      page: 1,
      page_size: 50,
      report_timezone: "Asia/Dushanbe",
    };
    mocks.get.mockResolvedValue({ data });
    expect(await listPhysicalDispositionHistory({ branch_id: scope.branchId }, scope)).toEqual(
      data,
    );
    for (const changed of [
      { ...data, report_timezone: "Not/AZone" },
      { ...data, report_timezone: null },
      { ...data, page: 2 },
      { ...data, total: 0 },
      { ...data, items: [item, item], total: 2 },
      { ...data, items: [{ ...item, request_hash: "0".repeat(64) }] },
      { ...data, items: [{ ...item, branch_id: uuid(90) }] },
      { ...data, items: [{ ...item, tenant_id: uuid(90) }] },
    ]) {
      mocks.get.mockResolvedValue({ data: changed });
      await expect(
        listPhysicalDispositionHistory({ branch_id: scope.branchId }, scope),
      ).rejects.toThrow();
    }
    await expect(listPhysicalDispositionHistory({ page_size: 201 }, scope)).rejects.toThrow();
    mocks.get.mockResolvedValue({
      data: { items: [], total: 0, page: 1, page_size: 50, report_timezone: null },
    });
    expect((await listPhysicalDispositionHistory({}, scope)).report_timezone).toBeNull();
  });
});
