import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  legacyCountSchema,
  previewLegacyReconciliation,
  readLegacyReconciliation,
  type LegacyCount,
  type LegacyPreview,
} from "@/features/inventory/legacyTransition";

const mocks = vi.hoisted(() => ({ post: vi.fn(), get: vi.fn() }));
vi.mock("@/lib/api", () => ({ api: mocks, withAuthSession: () => ({}) }));
const id = (n: number) => `aaaaaaaa-aaaa-4aaa-8aaa-${String(n).padStart(12, "0")}`;
const context = {
  tenant_id: id(1),
  branch_id: id(2),
  catalog_id: id(3),
  batch_id: id(4),
  actor_id: id(5),
};
function count(): LegacyCount {
  return {
    packaging: {
      version: 2,
      unit: "tablet",
      units_per_pack: 40,
      units_per_blister: 10,
      dispense_step: 1,
    },
    closed_packs: 0,
    opened_packs: [
      {
        id: id(6),
        opened_at: "2026-10-01T10:00:00.123456+05:00",
        intact_blisters: [3, 1],
        opened_blisters: [
          { number: 4, remaining_units: 5 },
          { number: 2, remaining_units: 5 },
        ],
        loose_units: 0,
      },
    ],
  };
}
function preview(value = count()): LegacyPreview {
  const scope = {
    tenant_id: context.tenant_id,
    branch_id: context.branch_id,
    catalog_id: context.catalog_id,
    batch_id: context.batch_id,
  };
  return {
    source: { ...scope, legacy_quantity: "0.750" },
    observation: { ...scope, ...value },
    recorded_base_units: "30.000",
    observed_base_units: 30,
    discrepancy_base_units: "0.000",
    fractional_base_units: "0.000",
    recorded_quantity_representable: true,
    comparison: "matched",
  };
}
beforeEach(() => vi.clearAllMocks());

describe("opened legacy count contract", () => {
  it("accepts zero sealed packs and explicit partial opened composition", () => {
    expect(legacyCountSchema.parse(count())).toEqual(count());
  });
  it.each([
    "duplicate UUID",
    "duplicate intact number",
    "overlapping number",
    "outside pack",
    "full opened blister",
    "zero remainder",
    "loose with blister",
    "unknown composition with blisters",
    "over capacity",
    "missing timezone",
    "UTC underflow",
    "UTC overflow",
    "excess precision",
  ])("rejects %s before transport", async (reason) => {
    const value = count();
    const pack = value.opened_packs[0];
    if (reason === "duplicate UUID")
      value.opened_packs.push({ ...pack, id: pack.id.toUpperCase() });
    if (reason === "duplicate intact number") pack.intact_blisters = [1, 1];
    if (reason === "overlapping number") pack.intact_blisters = [2];
    if (reason === "outside pack") pack.intact_blisters = [5];
    if (reason === "full opened blister") pack.opened_blisters[0].remaining_units = 10;
    if (reason === "zero remainder") pack.opened_blisters[0].remaining_units = 0;
    if (reason === "loose with blister") pack.loose_units = 1;
    if (reason === "unknown composition with blisters") value.packaging.units_per_blister = null;
    if (reason === "over capacity") {
      value.packaging.units_per_blister = null;
      pack.intact_blisters = [];
      pack.opened_blisters = [];
      pack.loose_units = 41;
    }
    if (reason === "missing timezone") pack.opened_at = "2026-10-01T10:00:00";
    if (reason === "UTC underflow") pack.opened_at = "0001-01-01T00:00:00+23:59";
    if (reason === "UTC overflow") pack.opened_at = "9999-12-31T23:59:59-23:59";
    if (reason === "excess precision") pack.opened_at = "2026-10-01T10:00:00.1234567Z";
    await expect(previewLegacyReconciliation(context, value)).rejects.toThrow();
    expect(mocks.post).not.toHaveBeenCalled();
  });
  it("accepts an explicit loose remainder when blister composition is unknown", () => {
    const value = count();
    value.packaging.units_per_blister = null;
    Object.assign(value.opened_packs[0], {
      intact_blisters: [],
      opened_blisters: [],
      loose_units: 15,
    });
    expect(legacyCountSchema.parse(value)).toEqual(value);
  });
  it("accepts canonical ordering and equivalent timezone without losing microseconds", async () => {
    const response = preview();
    const pack = response.observation.opened_packs[0];
    pack.opened_at = "2026-10-01T05:00:00.123456Z";
    pack.intact_blisters.reverse();
    pack.opened_blisters.reverse();
    mocks.post.mockResolvedValue({ data: response });
    await expect(previewLegacyReconciliation(context, count())).resolves.toEqual(response);
  });
  it.each(["UUID", "timestamp microseconds", "blister identity", "remainder"])(
    "rejects mutated %s in preview and recovery",
    async (field) => {
      const response = preview();
      const pack = response.observation.opened_packs[0];
      if (field === "UUID") pack.id = id(20);
      if (field === "timestamp microseconds") pack.opened_at = "2026-10-01T05:00:00.123457Z";
      if (field === "blister identity") {
        pack.intact_blisters = [2, 1];
        pack.opened_blisters[1].number = 3;
      }
      if (field === "remainder") pack.opened_blisters[0].remaining_units = 4;
      mocks.post.mockResolvedValue({ data: response });
      await expect(previewLegacyReconciliation(context, count())).rejects.toThrow(
        "другой пересчёт",
      );
      mocks.get.mockResolvedValue({
        data: {
          id: id(7),
          created_at: "2026-10-15T10:00:00Z",
          created_by: context.actor_id,
          preview: response,
        },
      });
      await expect(
        readLegacyReconciliation(context, id(7), { count: count(), quantity: "0.750" }),
      ).rejects.toThrow("не совпадает");
    },
  );
});
