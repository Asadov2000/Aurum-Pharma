import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const listCustomerReturns = vi.fn();
const resolveCustomerReturn = vi.fn();
const authResult = {
  user: {
    is_developer: false,
    permissions: ["customer_returns.view", "customer_returns.resolve"] as string[],
  },
};

vi.mock("@/features/auth/hooks", () => ({
  useAuth: () => authResult,
}));

vi.mock("@/features/customerReturns/api", () => ({
  listCustomerReturns: (...args: unknown[]) => listCustomerReturns(...args),
  resolveCustomerReturn: (...args: unknown[]) => resolveCustomerReturn(...args),
}));

vi.mock("@/features/foundation/api", () => ({
  listBranches: vi.fn().mockResolvedValue([]),
}));

import { CustomerReturnsPanel } from "@/features/customerReturns/CustomerReturnsPanel";

const RETURN_ITEM = {
  id: "00000000-0000-4000-8000-000000000001",
  branch_id: "00000000-0000-4000-8000-000000000002",
  branch_name: "Аптека Рудаки",
  return_sale_id: "00000000-0000-4000-8000-000000000003",
  return_receipt_number: "R-101",
  parent_sale_id: "00000000-0000-4000-8000-000000000004",
  parent_receipt_number: "S-100",
  catalog_id: "00000000-0000-4000-8000-000000000005",
  catalog_name: "Парацетамол",
  catalog_form: "таблетки",
  catalog_dosage: "500 мг",
  batch_id: "00000000-0000-4000-8000-000000000006",
  batch_number: "LOT-2026-01",
  expires_at: "2027-06-30",
  qty: "1.000",
  refund_reason: "Повреждена упаковка",
  refund_comment: null,
  received_at: "2026-08-29T10:00:00Z",
  status: "pending" as const,
  disposition_type: null,
  disposition_reason: null,
  disposition_comment: null,
  resolved_at: null,
};

function renderPanel() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <CustomerReturnsPanel />
    </QueryClientProvider>,
  );
}

describe("CustomerReturnsPanel", () => {
  beforeEach(() => {
    authResult.user.permissions = ["customer_returns.view", "customer_returns.resolve"];
    listCustomerReturns.mockReset();
    listCustomerReturns.mockResolvedValue({
      items: [RETURN_ITEM],
      total: 1,
      pending: 1,
      resolved: 0,
      page: 1,
      page_size: 25,
    });
    resolveCustomerReturn.mockReset();
    resolveCustomerReturn.mockResolvedValue({
      ...RETURN_ITEM,
      status: "resolved",
      disposition_type: "disposed",
    });
  });

  it("records an irreversible physical disposition with an idempotency key", async () => {
    renderPanel();

    fireEvent.click((await screen.findAllByRole("button", { name: "Принять решение" }))[0]!);
    fireEvent.click(screen.getByRole("radio", { name: "Утилизировано" }));
    fireEvent.change(screen.getByLabelText("Причина"), {
      target: { value: "damaged" },
    });
    fireEvent.click(
      screen.getByRole("checkbox", {
        name: "Подтверждаю, что указанное действие фактически выполнено",
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить действие" }));

    await waitFor(() => expect(resolveCustomerReturn).toHaveBeenCalledTimes(1));
    expect(resolveCustomerReturn).toHaveBeenCalledWith(
      RETURN_ITEM.id,
      expect.objectContaining({
        operation_id: expect.any(String),
        disposition_type: "disposed",
        reason_code: "damaged",
        comment: null,
      }),
    );
  });

  it("does not expose resolution controls without the dedicated permission", async () => {
    authResult.user.permissions = ["customer_returns.view"];
    renderPanel();

    expect((await screen.findAllByText("Парацетамол")).length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: "Принять решение" })).not.toBeInTheDocument();
  });
});
