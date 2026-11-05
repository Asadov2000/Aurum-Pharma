import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { type PropsWithChildren } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { type MeResponse } from "@/features/auth/types";
import { listMixedCustomerReturns } from "@/features/customerReturns/api";
import { useMixedCustomerReturnsQuery } from "@/features/customerReturns/queries";
import {
  type MixedCustomerReturnItem,
  type MixedCustomerReturnList,
} from "@/features/customerReturns/types";

const auth = vi.hoisted(() => ({ user: null as MeResponse | null }));
vi.mock("@/features/auth/hooks", () => ({ useAuth: () => ({ user: auth.user }) }));
vi.mock("@/features/customerReturns/api", () => ({
  listMixedCustomerReturns: vi.fn(),
  listCustomerReturns: vi.fn(),
  listPhysicalCustomerReturns: vi.fn(),
  resolveCustomerReturn: vi.fn(),
}));
const fetchQueue = vi.mocked(listMixedCustomerReturns);
const uuid = (value: number): string =>
  `00000000-0000-4000-8000-${String(value).padStart(12, "0")}`;
const branchA = uuid(1);
const branchB = uuid(2);
let client: QueryClient;

function actor(): MeResponse {
  return {
    id: uuid(3),
    email: "scope-test@example.test",
    full_name: "Scope test",
    is_developer: false,
    is_administrator: false,
    home_tenant_id: uuid(4),
    active_tenant_id: uuid(4),
    status: "active",
    last_login_at: null,
    level: 1,
    is_tenant_owner: false,
    branch_assignments: {},
    permissions: ["customer_returns.view"],
    permission_scopes: { "customer_returns.view": null },
    platform_capabilities: [],
    support_access: null,
  };
}
function row(branchId: string, name: string): MixedCustomerReturnItem {
  return {
    operation_id: uuid(5),
    line_id: uuid(6),
    inventory_kind: "legacy_pack",
    batch_id: uuid(7),
    lot_id: null,
    legacy_quantity: "0.125",
    base_units: null,
    unit_label: "уп.",
    branch_id: branchId,
    branch_name: "Test branch",
    report_timezone: "Asia/Dushanbe",
    parent_sale_id: uuid(8),
    parent_receipt_number: "TEST-001",
    return_sale_id: null,
    return_receipt_number: null,
    catalog_id: uuid(9),
    catalog_name: name,
    batch_number: null,
    expires_at: "2027-06-30",
    received_at: "2026-10-09T10:00:00Z",
    refund_reason: "quality_issue",
    refund_comment: null,
    intake_kind: "zero_return",
    status: "pending",
  };
}
function queue(...items: MixedCustomerReturnItem[]): MixedCustomerReturnList {
  return { items, total: items.length, page: 1, page_size: 25 };
}
function deferredQueue(): {
  promise: Promise<MixedCustomerReturnList>;
  resolve: (value: MixedCustomerReturnList) => void;
} {
  let resolve!: (value: MixedCustomerReturnList) => void;
  const promise = new Promise<MixedCustomerReturnList>((accept) => {
    resolve = accept;
  });
  return { promise, resolve };
}
function wrapper({ children }: PropsWithChildren): JSX.Element {
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

describe("mixed queue authorization scope", () => {
  beforeEach(() => {
    fetchQueue.mockReset();
    auth.user = actor();
    client = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: Infinity, staleTime: Infinity } },
    });
  });
  afterEach(() => {
    cleanup();
    client.clear();
    auth.user = null;
  });

  it.each(["narrower branches", "another user", "another tenant"] as const)(
    "hides previously cached rows for %s while the authorized fetch is pending",
    async (change) => {
      const original = queue(
        row(branchA, "Previously visible A"),
        row(branchB, "Previously visible B"),
      );
      const next = queue(row(branchB, "Currently visible"));
      const pending = deferredQueue();
      fetchQueue.mockResolvedValueOnce(original).mockReturnValueOnce(pending.promise);
      const { result, rerender } = renderHook(
        () => useMixedCustomerReturnsQuery({ page: 1, page_size: 25 }),
        { wrapper },
      );
      await waitFor(() => expect(result.current.data).toEqual(original));

      const current = actor();
      auth.user =
        change === "narrower branches"
          ? { ...current, permission_scopes: { "customer_returns.view": [branchB] } }
          : change === "another user"
            ? { ...current, id: uuid(10) }
            : { ...current, active_tenant_id: uuid(11) };
      rerender();
      await waitFor(() => expect(fetchQueue).toHaveBeenCalledTimes(2));
      expect(result.current.isPending).toBe(true);
      expect(result.current.data).toBeUndefined();
      // The old authorized data still exists in cache: key isolation and absence
      // of cross-scope placeholder data must protect the currently mounted hook.
      expect(
        client
          .getQueryCache()
          .getAll()
          .some((query) => query.state.data === original),
      ).toBe(true);
      await act(async () => pending.resolve(next));
      await waitFor(() => expect(result.current.data).toEqual(next));
      expect(result.current.data?.items.map((item) => item.catalog_name)).toEqual([
        "Currently visible",
      ]);
    },
  );

  it("does not fetch or expose the previous queue after view permission is revoked", async () => {
    const original = queue(row(branchA, "Previously authorized"));
    fetchQueue.mockResolvedValueOnce(original);
    const { result, rerender } = renderHook(
      () => useMixedCustomerReturnsQuery({ page: 1, page_size: 25 }),
      { wrapper },
    );
    await waitFor(() => expect(result.current.data).toEqual(original));
    auth.user = { ...actor(), permissions: [], permission_scopes: {} };
    rerender();
    expect(result.current.data).toBeUndefined();
    expect(result.current.fetchStatus).toBe("idle");
    expect(fetchQueue).toHaveBeenCalledTimes(1);
  });
});
