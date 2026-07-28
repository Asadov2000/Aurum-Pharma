import { beforeEach, describe, expect, it, vi } from "vitest";

const post = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    post: (...args: unknown[]) => post(...args),
  },
}));

import { searchBranches, searchRegisters } from "@/features/foundation/api";
import { listUsers } from "@/features/roles/api";
import { searchSuppliers } from "@/features/suppliers/api";

describe("private management searches", () => {
  beforeEach(() => {
    post.mockReset();
    post.mockResolvedValue({ data: { items: [], total: 0, page: 1, page_size: 25 } });
  });

  it("keeps user and supplier search values out of URL query parameters", async () => {
    const signal = new AbortController().signal;

    await listUsers({ q: "Иван", status: "active", page: 1, page_size: 25 }, signal);
    await searchSuppliers({ q: "+992900001122", is_active: true }, signal);

    expect(post).toHaveBeenNthCalledWith(
      1,
      "/users/search",
      expect.objectContaining({ q: "Иван", status: "active" }),
      { signal },
    );
    expect(post).toHaveBeenNthCalledWith(
      2,
      "/suppliers/search",
      expect.objectContaining({ q: "+992900001122", is_active: true }),
      { signal },
    );
  });

  it("uses cancellable body-based searches for branches and registers", async () => {
    const signal = new AbortController().signal;

    await searchBranches({ q: "Рудаки", branch_type: "pharmacy" }, signal);
    await searchRegisters({ q: "Касса", printer_type: "thermal_80" }, signal);

    expect(post).toHaveBeenNthCalledWith(
      1,
      "/branches/search",
      expect.objectContaining({ q: "Рудаки", branch_type: "pharmacy" }),
      { signal },
    );
    expect(post).toHaveBeenNthCalledWith(
      2,
      "/registers/search",
      expect.objectContaining({ q: "Касса", printer_type: "thermal_80" }),
      { signal },
    );
  });
});
