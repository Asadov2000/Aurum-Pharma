import { beforeEach, describe, expect, it, vi } from "vitest";

const get = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", () => ({
  api: { get },
}));

import { searchAudit } from "@/features/audit/api";

const EMPTY_PAGE = {
  items: [],
  total: 0,
  page: 1,
  page_size: 50,
};

describe("audit API", () => {
  beforeEach(() => {
    get.mockReset();
    get.mockResolvedValue({ data: EMPTY_PAGE });
  });

  it("routes global audit through the administrative namespace", async () => {
    await searchAudit({ scope: "global", tenant_id: "tenant-1" });

    expect(get).toHaveBeenCalledWith(
      "/admin/audit/global",
      expect.objectContaining({
        params: expect.objectContaining({ tenant_id: "tenant-1" }),
      }),
    );
  });

  it("keeps tenant audit on the tenant data path", async () => {
    await searchAudit({ scope: "tenant", tenant_id: "ignored" });

    expect(get).toHaveBeenCalledWith(
      "/audit/tenant",
      expect.objectContaining({
        params: expect.objectContaining({ tenant_id: undefined }),
      }),
    );
  });
});
