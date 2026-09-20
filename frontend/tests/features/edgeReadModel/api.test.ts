import { beforeEach, describe, expect, it, vi } from "vitest";

const get = vi.fn();

vi.mock("@/lib/api", () => ({
  api: { get: (...args: unknown[]) => get(...args) },
}));

import {
  listEdgeCatalog,
  listEdgeInventory,
  listEdgeSuppliers,
} from "@/features/edgeReadModel/api";
import { clearEdgeSnapshotMetadata } from "@/features/edgeReadModel/snapshotStore";

const metadata = {
  snapshot_id: "20000000-0000-4000-8000-000000000003",
  source_revision: 7,
  source_generated_at: "2026-09-20T09:00:00Z",
  last_confirmed_at: "2026-09-20T10:00:00Z",
  age_seconds: 30,
  is_stale: false,
  source: "local_edge" as const,
};

describe("Edge read-model API", () => {
  beforeEach(() => {
    get.mockReset();
    clearEdgeSnapshotMetadata();
  });

  it("binds catalog and supplier reads to the configured branch", async () => {
    get.mockResolvedValue({
      data: { metadata, items: [], total: 0, page: 2, page_size: 50 },
    });
    const signal = new AbortController().signal;
    const params = {
      branchId: "20000000-0000-4000-8000-000000000002",
      page: 2,
      pageSize: 50,
      query: "пара",
    };

    await listEdgeCatalog(params, signal);
    await listEdgeSuppliers(params, signal);

    expect(get).toHaveBeenNthCalledWith(1, "/edge/read-model/catalog", {
      signal,
      params: { branch_id: params.branchId, page: 2, page_size: 50, q: "пара" },
    });
    expect(get).toHaveBeenNthCalledWith(2, "/edge/read-model/suppliers", {
      signal,
      params: { branch_id: params.branchId, page: 2, page_size: 50, q: "пара" },
    });
  });

  it("requests inventory without inventing unavailable filters", async () => {
    get.mockResolvedValue({
      data: {
        metadata,
        items: [],
        total: 0,
        page: 1,
        page_size: 50,
        summary: {
          total_qty: "0",
          purchase_value: null,
          sale_value: "0",
          attention_count: 0,
          expired_count: 0,
          blocked_count: 0,
        },
      },
    });

    await listEdgeInventory({
      branchId: "20000000-0000-4000-8000-000000000002",
      showEmpty: true,
    });

    expect(get).toHaveBeenCalledWith("/edge/read-model/inventory", {
      signal: undefined,
      params: {
        branch_id: "20000000-0000-4000-8000-000000000002",
        page: 1,
        page_size: 50,
        show_empty: true,
      },
    });
  });
});
