import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { listPhysicalCustomerReturns } from "@/features/customerReturns/api";
import { CustomerReturnsPanel } from "@/features/customerReturns/CustomerReturnsPanel";
import { type PhysicalCustomerReturnItem } from "@/features/customerReturns/types";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  permissions: ["customer_returns.view", "customer_returns.resolve", "branches.view"] as string[],
}));
vi.mock("@/lib/api", () => ({ api: { get: mocks.get, post: mocks.post } }));
vi.mock("@/features/auth/hooks", () => ({
  useAuth: () => ({ user: { is_developer: false, permissions: mocks.permissions } }),
}));
vi.mock("@/features/foundation/api", () => ({
  listBranches: vi
    .fn()
    .mockResolvedValue([{ id: "00000000-0000-4000-8000-000000000004", name: "Аптека Рудаки" }]),
}));
const uuid = (value: number): string =>
  `00000000-0000-4000-8000-${String(value).padStart(12, "0")}`;
const zero: PhysicalCustomerReturnItem = {
  operation_id: uuid(1),
  line_id: uuid(2),
  lot_id: uuid(3),
  branch_id: uuid(4),
  branch_name: "Аптека Рудаки",
  report_timezone: "Asia/Dushanbe",
  parent_sale_id: uuid(5),
  parent_receipt_number: "S-001",
  return_sale_id: null,
  return_receipt_number: null,
  catalog_id: uuid(6),
  catalog_name: "Парацетамол",
  base_units: 7,
  unit_label: "табл.",
  batch_number: "LOT-2026",
  expires_at: "2027-06-30",
  received_at: "2026-10-09T10:00:00Z",
  refund_reason: "quality_issue",
  refund_comment: "Повреждена первичная упаковка",
  intake_kind: "zero_return",
  status: "pending",
};
const cash: PhysicalCustomerReturnItem = {
  ...zero,
  operation_id: uuid(7),
  lot_id: uuid(8),
  catalog_name: "Амоксициллин",
  base_units: 2,
  unit_label: "капс.",
  intake_kind: "cash_refund",
  return_sale_id: uuid(9),
  return_receipt_number: "R-001",
};
function list(items = [zero, cash], total = items.length) {
  return { items, total, page: 1, page_size: 25 };
}
function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <CustomerReturnsPanel />
    </QueryClientProvider>,
  );
}
async function selectPhysical(): Promise<void> {
  fireEvent.click(screen.getByRole("button", { name: "Физические возвраты" }));
  await screen.findByRole("table", { name: "Очередь физических возвратов" });
}
describe("physical quarantine queue", () => {
  afterEach(cleanup);
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    mocks.permissions = ["customer_returns.view", "customer_returns.resolve", "branches.view"];
    mocks.get.mockImplementation((url: string) =>
      Promise.resolve({
        data:
          url === "/customer-returns/physical"
            ? list()
            : { items: [], total: 0, pending: 0, resolved: 0, page: 1, page_size: 25 },
      }),
    );
  });
  it("requests only the selected queue and keeps physical rows out of legacy summaries and actions", async () => {
    renderPanel();
    await screen.findByText("Очередь пуста");
    expect(mocks.get.mock.calls.map(([url]) => url)).toEqual(["/customer-returns"]);
    await selectPhysical();
    expect(screen.getByRole("button", { name: "Физические возвраты" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(mocks.get.mock.calls.map(([url]) => url)).toEqual([
      "/customer-returns",
      "/customer-returns/physical",
    ]);
    expect(screen.queryByLabelText("Статус")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Сводка по возвратам")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Принять решение" })).not.toBeInTheDocument();
    expect(mocks.post).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Возвраты упаковками" }));
    await screen.findByLabelText("Статус");
    expect(
      screen.queryByRole("table", { name: "Очередь физических возвратов" }),
    ).not.toBeInTheDocument();
  });
  it("shows historical unit counts, cash receipt and zero intake without inventing a receipt in table and cards", async () => {
    renderPanel();
    await selectPhysical();
    const table = screen.getByRole("table", { name: "Очередь физических возвратов" });
    expect(screen.getByLabelText("Поиск")).toHaveAttribute("maxlength", "160");
    expect(within(table).getByText("7 табл.")).toBeInTheDocument();
    expect(within(table).getByText("2 капс.")).toBeInTheDocument();
    expect(within(table).getByText("Принят без выплаты")).toBeInTheDocument();
    expect(within(table).getByText("Возврат №R-001")).toBeInTheDocument();
    expect(within(table).queryByText("Возврат №—")).not.toBeInTheDocument();
    expect(within(table).getAllByText(/15:00/)).toHaveLength(2);
    const card = screen.getByRole("article", { name: "Парацетамол, физический возврат" });
    expect(within(card).getByText("7 табл.")).toBeInTheDocument();
    expect(within(card).getByText("Принят без выплаты")).toBeInTheDocument();
    expect(within(card).getByText("Повреждена первичная упаковка")).toBeInTheDocument();
    expect(within(card).getByText("30.06.2027")).toBeInTheDocument();
    expect(screen.getByText(/Товар изолирован от продажи/)).toBeInTheDocument();
    expect(mocks.post).not.toHaveBeenCalled();
  });
  it("uses its own pagination/search/branch filters and resets page on a filter change", async () => {
    mocks.get.mockImplementation((url: string) =>
      Promise.resolve({
        data:
          url === "/customer-returns/physical"
            ? list([zero], 26)
            : { items: [], total: 0, pending: 0, resolved: 0, page: 1, page_size: 25 },
      }),
    );
    renderPanel();
    await selectPhysical();
    fireEvent.click(screen.getByRole("button", { name: "Вперёд" }));
    await waitFor(() =>
      expect(mocks.get).toHaveBeenLastCalledWith("/customer-returns/physical", {
        params: { branch_id: undefined, search: undefined, page: 2, page_size: 25 },
      }),
    );
    fireEvent.change(screen.getByLabelText("Поиск"), { target: { value: "  LOT-2026  " } });
    await waitFor(() =>
      expect(mocks.get).toHaveBeenLastCalledWith("/customer-returns/physical", {
        params: { branch_id: undefined, search: "LOT-2026", page: 1, page_size: 25 },
      }),
    );
    fireEvent.change(screen.getByLabelText("Точка"), { target: { value: zero.branch_id } });
    await waitFor(() =>
      expect(mocks.get).toHaveBeenLastCalledWith("/customer-returns/physical", {
        params: { branch_id: zero.branch_id, search: "LOT-2026", page: 1, page_size: 25 },
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: /Сбросить всё/ }));
    await waitFor(() =>
      expect(mocks.get).toHaveBeenLastCalledWith("/customer-returns/physical", {
        params: { branch_id: undefined, search: undefined, page: 1, page_size: 25 },
      }),
    );
  });
  it("hides branch filtering without permission while rows still use the pharmacy timezone", async () => {
    mocks.permissions = ["customer_returns.view"];
    renderPanel();
    await selectPhysical();
    expect(screen.queryByLabelText("Точка")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Принять решение" })).not.toBeInTheDocument();
    expect(screen.getAllByText(/15:00/).length).toBeGreaterThan(0);
  });
  it("shows a retryable error without mutations and recovers through GET", async () => {
    let attempts = 0;
    mocks.get.mockImplementation((url: string) => {
      if (url !== "/customer-returns/physical")
        return Promise.resolve({
          data: { items: [], total: 0, pending: 0, resolved: 0, page: 1, page_size: 25 },
        });
      attempts += 1;
      return attempts === 1
        ? Promise.reject(new Error("Offline"))
        : Promise.resolve({ data: list() });
    });
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: "Физические возвраты" }));
    await screen.findByRole("alert");
    fireEvent.click(screen.getByRole("button", { name: "Повторить" }));
    await screen.findByRole("table", { name: "Очередь физических возвратов" });
    expect(attempts).toBe(2);
    expect(mocks.post).not.toHaveBeenCalled();
  });
  it("shows loading, empty quarantine and filtered empty states", async () => {
    let resolveList: ((value: { data: ReturnType<typeof list> }) => void) | undefined;
    mocks.get.mockImplementation((url: string) =>
      url === "/customer-returns/physical"
        ? new Promise<{ data: ReturnType<typeof list> }>((resolve) => {
            resolveList = resolve;
          })
        : Promise.resolve({
            data: { items: [], total: 0, pending: 0, resolved: 0, page: 1, page_size: 25 },
          }),
    );
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: "Физические возвраты" }));
    expect(
      screen.getByRole("status", { name: "Загрузка физических возвратов" }),
    ).toBeInTheDocument();
    await waitFor(() => expect(resolveList).toBeDefined());
    resolveList!({ data: list([]) });
    await screen.findByText("Физических возвратов в карантине нет.");
    mocks.get.mockResolvedValue({ data: list([]) });
    fireEvent.change(screen.getByLabelText("Поиск"), { target: { value: "нет товара" } });
    await screen.findByText("Возвраты не найдены");
    expect(screen.queryByRole("button", { name: "Принять решение" })).not.toBeInTheDocument();
  });
});

describe("physical queue response contract", () => {
  it("accepts the backend page size limit of 200 and rejects larger pages", async () => {
    mocks.get.mockResolvedValue({ data: { ...list(), page_size: 200 } });
    expect((await listPhysicalCustomerReturns({ page_size: 200 })).page_size).toBe(200);
    mocks.get.mockResolvedValue({ data: { ...list(), page_size: 201 } });
    await expect(listPhysicalCustomerReturns({ page_size: 201 })).rejects.toThrow();
  });
  it.each(["negative_units", "fractional_units", "zero_receipt", "resolved", "unknown_field"])(
    "rejects invalid %s",
    async (field) => {
      const item =
        field === "negative_units"
          ? { ...zero, base_units: -1 }
          : field === "fractional_units"
            ? { ...zero, base_units: 1.5 }
            : field === "zero_receipt"
              ? { ...zero, return_sale_id: uuid(90), return_receipt_number: "0" }
              : field === "resolved"
                ? { ...zero, status: "resolved" }
                : { ...zero, total_cost_amount: "1.00" };
      mocks.get.mockResolvedValue({ data: list([item as PhysicalCustomerReturnItem]) });
      await expect(listPhysicalCustomerReturns({ page: 1 })).rejects.toThrow();
    },
  );
});
