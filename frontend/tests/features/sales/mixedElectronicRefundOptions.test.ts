import { beforeEach, describe, expect, it, vi } from "vitest";
import { getMixedElectronicRefundOptions } from "@/features/sales/mixedElectronicRefundOptions";
import { api } from "@/lib/api";

const session = vi.hoisted(() => ({ active: true }));
vi.mock("@/features/auth/logoutState", () => ({ getAuthEpoch: () => "test" }));
vi.mock("@/features/pos/posStorageScope", () => ({
  capturePosSessionGuard: () => () => session.active,
}));
vi.mock("@/stores/auth", () => ({
  useAuthStore: {
    getState: () => ({
      accessToken: "test",
      user: {
        is_developer: false,
        permissions: ["pos.refund"],
        permission_scopes: { "pos.refund": null },
      },
    }),
  },
}));
vi.mock("@/lib/api", () => ({
  api: { get: vi.fn(), post: vi.fn() },
  withAuthSession: (config: object) => ({ ...config, _auth: { generation: 1 } }),
}));
const id = (n: number) => `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
const scope = () => ({
  tenantId: id(1),
  userId: id(2),
  parentSaleId: id(3),
  branchId: id(4),
  registerId: id(5),
});
const options = () => ({
  parent_sale_id: id(3),
  branch_id: id(4),
  register_id: id(5),
  original_receipt_number: "S-1",
  reason_mode: "optional",
  lines: [
    {
      inventory_kind: "legacy_pack",
      line_id: id(6),
      name: "Упаковка",
      unit_label: "уп.",
      original_retail_unit_price: "2.00",
      sold_quantity: "2.000",
      returned_quantity: "0.250",
      remaining_quantity: "1.750",
    },
  ],
});
beforeEach(() => {
  vi.clearAllMocks();
  session.active = true;
});
describe("mixed electronic return options", () => {
  it("uses only the electronic options endpoint and pins session", async () => {
    vi.mocked(api.get).mockResolvedValue({ data: options() });
    const contextCheck = vi.fn();
    expect(await getMixedElectronicRefundOptions({ ...scope(), contextCheck })).toEqual(options());
    expect(contextCheck).toHaveBeenCalledTimes(2);
    expect(api.get).toHaveBeenCalledWith(`/sales/${id(3)}/mixed-electronic-refunds/options`, {
      timeout: 8000,
      _auth: { generation: 1 },
    });
    expect(api.post).not.toHaveBeenCalled();
  });
  it.each(["parent_sale_id", "branch_id", "register_id"])("rejects different %s", async (field) => {
    vi.mocked(api.get).mockResolvedValue({ data: { ...options(), [field]: id(99) } });
    await expect(getMixedElectronicRefundOptions(scope())).rejects.toThrow();
  });
  it("rejects malformed remaining quantities and extra fields", async () => {
    vi.mocked(api.get).mockResolvedValueOnce({
      data: { ...options(), lines: [{ ...options().lines[0], remaining_quantity: "1.751" }] },
    });
    await expect(getMixedElectronicRefundOptions(scope())).rejects.toThrow();
    vi.mocked(api.get).mockResolvedValueOnce({ data: { ...options(), token: "unexpected" } });
    await expect(getMixedElectronicRefundOptions(scope())).rejects.toThrow();
  });
  it("blocks before dispatch and discards responses after context changes", async () => {
    session.active = false;
    await expect(getMixedElectronicRefundOptions(scope())).rejects.toThrow();
    expect(api.get).not.toHaveBeenCalled();
    session.active = true;
    vi.mocked(api.get).mockImplementationOnce(async () => {
      session.active = false;
      return { data: options() };
    });
    await expect(getMixedElectronicRefundOptions(scope())).rejects.toThrow();
  });
  it("rejects unknown scope keys and non-callable context checks before transport", async () => {
    const extra = { ...scope(), unsafe: true };
    await expect(getMixedElectronicRefundOptions(extra)).rejects.toThrow();
    await expect(
      getMixedElectronicRefundOptions({ ...scope(), contextCheck: 1 as unknown as () => void }),
    ).rejects.toThrow();
    expect(api.get).not.toHaveBeenCalled();
  });
});
