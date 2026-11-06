import { beforeEach, describe, expect, it, vi } from "vitest";

import { type CatalogItem } from "@/features/catalog/types";
import { type PhysicalCartOptions } from "@/features/pos/physicalCartOptions";
import { resolveCashierProduct } from "@/features/pos/resolveCashierProduct";

const optionsApi = vi.hoisted(() => vi.fn());
vi.mock("@/features/pos/physicalCartOptions", () => ({ getPhysicalCartOptions: optionsApi }));

const item: CatalogItem = {
  id: "catalog-a",
  tenant_id: "tenant-a",
  brand_name: "Аспирин",
  inn: null,
  manufacturer: null,
  form: null,
  dosage: null,
  pack_size: null,
  atx_code: null,
  dispensing_type: "otc",
  storage_type: "normal",
  category: null,
  base_price: "12.34",
  currency: "TJS",
  is_active: true,
  created_at: "2026-11-06T00:00:00Z",
  updated_at: "2026-11-06T00:00:00Z",
};

function options(): PhysicalCartOptions {
  return {
    state: "physical_options_preview",
    tenant_id: item.tenant_id,
    branch_id: "branch-a",
    sale_id: "sale-a",
    catalog_id: item.id,
    stock_date: "2026-11-06",
    packaging: null,
    currency: "TJS",
    checkout_enabled: false,
    stock_reserved: false,
    stock: { base_units: 0, closed_packs: 0, intact_blisters: null },
    options: (["pack", "blister", "unit"] as const).map((sale_format) => ({
      sale_format,
      configured: false,
      quantity_step: null,
      retail_unit_price: null,
      unavailable_reason: "not_prepared",
    })),
  };
}

function resolve(product = item, signal = new AbortController().signal) {
  return resolveCashierProduct("sale-a", "branch-a", product, signal);
}

beforeEach(() => {
  optionsApi.mockReset();
  optionsApi.mockResolvedValue(options());
});

describe("resolveCashierProduct", () => {
  it("identifies an ordinary item only after a matching server options response", async () => {
    const signal = new AbortController().signal;
    await expect(resolve(item, signal)).resolves.toEqual({ kind: "ordinary" });
    expect(optionsApi).toHaveBeenCalledExactlyOnceWith("sale-a", item.id, signal);
  });

  it("uses server physical packaging despite a missing catalog preparation snapshot", async () => {
    const response = options();
    response.packaging = {
      version: 2,
      unit: "tablet",
      units_per_pack: 30,
      units_per_blister: 10,
      dispense_step: 1,
    };
    response.options[0] = {
      sale_format: "pack",
      configured: true,
      quantity_step: 1,
      retail_unit_price: "30.00",
      unavailable_reason: null,
    };
    optionsApi.mockResolvedValue(response);
    const result = await resolve({ ...item, unit_sale_preparation: null });
    expect(result.kind).toBe("physical");
    if (result.kind === "physical") expect(result.options).toBe(response);
  });

  it("uses confirmed server configuration instead of an obsolete catalog hint", async () => {
    await expect(
      resolve({
        ...item,
        unit_sale_preparation: {
          version: 1,
          state: "prepared",
          unit: "tablet",
          units_per_pack: 30,
          units_per_blister: 10,
          dispense_step: 1,
          unit_price: "1.00",
        },
      }),
    ).resolves.toEqual({ kind: "ordinary" });
  });

  it.each(["sale_id", "branch_id", "catalog_id", "tenant_id"] as const)(
    "rejects a response attached to another %s",
    async (field) => {
      const response = options();
      response[field] = "another-owner";
      optionsApi.mockResolvedValue(response);
      await expect(resolve()).rejects.toThrow("Не удалось подтвердить параметры выбранного товара");
    },
  );

  it.each([{ is_active: false }, { deleted_at: "2026-11-01T00:00:00Z" }])(
    "does not request options for an unavailable catalog item %j",
    async (override) => {
      await expect(resolve({ ...item, ...override })).rejects.toThrow(
        "Товар недоступен для продажи",
      );
      expect(optionsApi).not.toHaveBeenCalled();
    },
  );

  it.each(["catalog_unavailable", "unknown_blister", "price_unavailable", null] as const)(
    "does not classify an unexplained %s option as an ordinary item",
    async (reason) => {
      const response = options();
      response.options[0].unavailable_reason = reason;
      optionsApi.mockResolvedValue(response);
      await expect(resolve()).rejects.toThrow("Товар недоступен для продажи");
    },
  );

  it("rejects a configured format without physical packaging", async () => {
    const response = options();
    response.options[0].configured = true;
    optionsApi.mockResolvedValue(response);
    await expect(resolve()).rejects.toThrow("Товар недоступен для продажи");
  });

  it.each([0, 1, 2, 4])(
    "rejects an incomplete or excessive format list of length %i",
    async (length) => {
      const response = options();
      response.options = Array.from({ length }, (_, index) => options().options[index % 3]!);
      optionsApi.mockResolvedValue(response);
      await expect(resolve()).rejects.toThrow("Товар недоступен для продажи");
    },
  );

  it("rejects duplicate sale formats rather than treating them as ordinary confirmation", async () => {
    const response = options();
    response.options = response.options.map((option) => ({ ...option, sale_format: "pack" }));
    optionsApi.mockResolvedValue(response);
    await expect(resolve()).rejects.toThrow("Товар недоступен для продажи");
  });

  it.each([
    new Error("network offline"),
    Object.assign(new Error("forbidden"), { isAxiosError: true, response: { status: 403 } }),
    Object.assign(new Error("not found"), { isAxiosError: true, response: { status: 404 } }),
    new DOMException("Request aborted", "AbortError"),
  ])("propagates %s without falling back to the ordinary ledger", async (failure) => {
    optionsApi.mockRejectedValue(failure);
    await expect(resolve()).rejects.toBe(failure);
    expect(optionsApi).toHaveBeenCalledOnce();
  });
});
