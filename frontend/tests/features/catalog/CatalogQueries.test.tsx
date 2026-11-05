import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ patch: vi.fn() }));
vi.mock("@/lib/api", () => ({ api }));

import { catalogKeys, useUpdateCatalogItem } from "@/features/catalog/queries";
import type { CatalogItem, CatalogItemWithBarcodes } from "@/features/catalog/types";

const ITEM: CatalogItem = {
  id: "item-1",
  tenant_id: "tenant-1",
  brand_name: "Тестовое лекарство",
  inn: null,
  manufacturer: null,
  form: null,
  dosage: null,
  pack_size: "№20",
  atx_code: null,
  dispensing_type: "otc",
  storage_type: "normal",
  category: null,
  base_price: "30.00",
  currency: "TJS",
  is_active: true,
  created_at: "2026-10-05T10:00:00Z",
  updated_at: "2026-10-05T10:00:00Z",
  unit_sale_preparation: null,
};
const CARD: CatalogItemWithBarcodes = {
  ...ITEM,
  barcodes: [{ id: "barcode-1", code: "LOT-A", code_type: "code128" }],
};
const UPDATED: CatalogItem = {
  ...ITEM,
  pack_size: "20 таблеток",
  unit_sale_preparation: {
    version: 1,
    state: "prepared",
    unit: "tablet",
    units_per_pack: 20,
    units_per_blister: 10,
    dispense_step: 1,
    unit_price: "1.50",
  },
};
const PAYLOAD = {
  pack_size: UPDATED.pack_size,
  unit_sale_preparation: UPDATED.unit_sale_preparation,
};
const KEY = catalogKeys.item(ITEM.id);

function setup(cached?: CatalogItemWithBarcodes) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  if (cached) client.setQueryData(KEY, cached);
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  const hook = renderHook(() => useUpdateCatalogItem(), { wrapper });
  return { client, result: hook.result };
}

describe("useUpdateCatalogItem detail cache", () => {
  beforeEach(() => {
    api.patch.mockReset();
    api.patch.mockResolvedValue({ data: UPDATED });
  });

  it("updates preparation and pack size immediately while retaining cached barcodes", async () => {
    const { client, result } = setup(CARD);
    await act(async () => {
      await result.current.mutateAsync({ id: ITEM.id, payload: PAYLOAD });
    });
    expect(api.patch).toHaveBeenCalledExactlyOnceWith("/catalog/item-1", PAYLOAD);
    expect(client.getQueryData(KEY)).toEqual({ ...UPDATED, barcodes: CARD.barcodes });
  });

  it("does not create an incomplete detail card when no detail cache exists", async () => {
    const { client, result } = setup();
    await act(async () => {
      await result.current.mutateAsync({ id: ITEM.id, payload: PAYLOAD });
    });
    expect(api.patch).toHaveBeenCalledTimes(1);
    expect(client.getQueryData(KEY)).toBeUndefined();
  });

  it("retains the original cached card after a failed PATCH", async () => {
    const failure = new Error("request failed");
    api.patch.mockRejectedValue(failure);
    const { client, result } = setup(CARD);
    await act(async () => {
      await expect(result.current.mutateAsync({ id: ITEM.id, payload: PAYLOAD })).rejects.toBe(
        failure,
      );
    });
    expect(client.getQueryData(KEY)).toEqual(CARD);
  });

  it("cancels an older detail GET so its late response cannot overwrite the saved composition", async () => {
    const { client, result } = setup(CARD);
    let pendingSignal: AbortSignal | undefined;
    let completeGet: ((card: CatalogItemWithBarcodes) => void) | undefined;
    const pendingGet = client
      .fetchQuery({
        queryKey: KEY,
        queryFn: ({ signal }) => {
          pendingSignal = signal;
          return new Promise<CatalogItemWithBarcodes>((resolve) => {
            completeGet = resolve;
          });
        },
      })
      .catch(() => undefined);
    expect(pendingSignal?.aborted).toBe(false);
    await act(async () => {
      await result.current.mutateAsync({ id: ITEM.id, payload: PAYLOAD });
    });
    expect(pendingSignal?.aborted).toBe(true);
    completeGet?.(CARD);
    await pendingGet;
    expect(client.getQueryData(KEY)).toEqual({ ...UPDATED, barcodes: CARD.barcodes });
  });
});
