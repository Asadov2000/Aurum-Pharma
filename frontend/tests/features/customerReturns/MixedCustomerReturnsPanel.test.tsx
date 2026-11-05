import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { listMixedCustomerReturns } from "@/features/customerReturns/api";
import { CustomerReturnsPanel } from "@/features/customerReturns/CustomerReturnsPanel";
import { type MixedCustomerReturnItem } from "@/features/customerReturns/types";

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
const zero: MixedCustomerReturnItem = {
  operation_id: uuid(1),
  line_id: uuid(2),
  inventory_kind: "legacy_pack",
  batch_id: uuid(3),
  lot_id: null,
  legacy_quantity: "0.125",
  branch_id: uuid(4),
  branch_name: "Аптека Рудаки",
  report_timezone: "Asia/Dushanbe",
  parent_sale_id: uuid(5),
  parent_receipt_number: "S-001",
  return_sale_id: null,
  return_receipt_number: null,
  catalog_id: uuid(6),
  catalog_name: "Парацетамол",
  base_units: null,
  unit_label: "уп.",
  batch_number: "LOT-2026",
  expires_at: "2027-06-30",
  received_at: "2026-10-09T10:00:00Z",
  refund_reason: "quality_issue",
  refund_comment: "Повреждена первичная упаковка",
  intake_kind: "zero_return",
  status: "pending",
};
const cash: MixedCustomerReturnItem = {
  ...zero,
  operation_id: uuid(7),
  inventory_kind: "physical_unit",
  batch_id: null,
  legacy_quantity: null,
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
async function selectMixed(): Promise<void> {
  fireEvent.click(screen.getByRole("button", { name: "Смешанные возвраты" }));
  await screen.findByRole("table", { name: "Очередь смешанных возвратов" });
}
describe("mixed quarantine queue", () => {
  afterEach(cleanup);
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    mocks.permissions = ["customer_returns.view", "customer_returns.resolve", "branches.view"];
    mocks.get.mockImplementation((url: string) =>
      Promise.resolve({
        data:
          url === "/customer-returns/mixed"
            ? list()
            : { items: [], total: 0, pending: 0, resolved: 0, page: 1, page_size: 25 },
      }),
    );
  });
  it("requests only the selected queue and keeps mixed rows out of legacy summaries and actions", async () => {
    renderPanel();
    await screen.findByText("Очередь пуста");
    expect(mocks.get.mock.calls.map(([url]) => url)).toEqual(["/customer-returns"]);
    await selectMixed();
    expect(screen.getByRole("button", { name: "Смешанные возвраты" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(mocks.get.mock.calls.map(([url]) => url)).toEqual([
      "/customer-returns",
      "/customer-returns/mixed",
    ]);
    expect(screen.queryByLabelText("Статус")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Сводка по возвратам")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Принять решение" })).not.toBeInTheDocument();
    expect(mocks.post).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Возвраты упаковками" }));
    await screen.findByLabelText("Статус");
    expect(
      screen.queryByRole("table", { name: "Очередь смешанных возвратов" }),
    ).not.toBeInTheDocument();
  });
  it("shows separate decimal pack and historical physical unit counts, cash receipt and zero intake without inventing a receipt in table and cards", async () => {
    renderPanel();
    await selectMixed();
    const table = screen.getByRole("table", { name: "Очередь смешанных возвратов" });
    expect(screen.getByLabelText("Поиск")).toHaveAttribute("maxlength", "160");
    expect(within(table).getByText("0,125 уп.")).toBeInTheDocument();
    expect(within(table).getByText("2 капс.")).toBeInTheDocument();
    expect(within(table).getByText("Принят без выплаты")).toBeInTheDocument();
    expect(within(table).getByText("Возврат №R-001")).toBeInTheDocument();
    expect(within(table).queryByText("Возврат №—")).not.toBeInTheDocument();
    expect(within(table).getAllByText(/15:00/)).toHaveLength(2);
    const card = screen.getByRole("article", { name: "Парацетамол, смешанный возврат" });
    expect(within(card).getByText("0,125 уп.")).toBeInTheDocument();
    expect(within(card).getByText("Принят без выплаты")).toBeInTheDocument();
    expect(within(card).getByText("Повреждена первичная упаковка")).toBeInTheDocument();
    expect(within(card).getByText("30.06.2027")).toBeInTheDocument();
    expect(screen.getByText(/Товар из смешанных продаж изолирован от продажи/)).toBeInTheDocument();
    expect(mocks.post).not.toHaveBeenCalled();
  });
  it("keeps all three queues separate when switching from mixed to physical", async () => {
    const defaultGet = mocks.get.getMockImplementation()!;
    mocks.get.mockImplementation((url: string) =>
      url === "/customer-returns/physical" ? Promise.resolve({ data: list([]) }) : defaultGet(url),
    );
    renderPanel();
    await selectMixed();
    fireEvent.click(screen.getByRole("button", { name: "Физические возвраты" }));
    await screen.findByText("Физических возвратов в карантине нет.");
    expect(
      screen.queryByRole("table", { name: "Очередь смешанных возвратов" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("Парацетамол")).not.toBeInTheDocument();
    await selectMixed();
    expect(
      screen.getByRole("region", { name: "Смешанные возвраты в карантине" }),
    ).toBeInTheDocument();
    expect(mocks.post).not.toHaveBeenCalled();
  });
  it("displays the largest legacy quantity without floating point rounding", async () => {
    mocks.get.mockResolvedValue({ data: list([{ ...zero, legacy_quantity: "99999999999.999" }]) });
    renderPanel();
    await selectMixed();
    const table = screen.getByRole("table", { name: "Очередь смешанных возвратов" });
    const card = screen.getByRole("article", { name: "Парацетамол, смешанный возврат" });
    expect(within(table).getByText(/^99\s999\s999\s999,999 уп\.$/)).toBeInTheDocument();
    expect(within(card).getByText(/^99\s999\s999\s999,999 уп\.$/)).toBeInTheDocument();
  });
  it("uses its own pagination/search/branch filters and resets page on a filter change", async () => {
    mocks.get.mockImplementation((url: string) =>
      Promise.resolve({
        data:
          url === "/customer-returns/mixed"
            ? list([zero], 26)
            : { items: [], total: 0, pending: 0, resolved: 0, page: 1, page_size: 25 },
      }),
    );
    renderPanel();
    await selectMixed();
    fireEvent.click(screen.getByRole("button", { name: "Вперёд" }));
    await waitFor(() =>
      expect(mocks.get).toHaveBeenLastCalledWith("/customer-returns/mixed", {
        params: { branch_id: undefined, search: undefined, page: 2, page_size: 25 },
      }),
    );
    fireEvent.change(screen.getByLabelText("Поиск"), { target: { value: "  LOT-2026  " } });
    await waitFor(() =>
      expect(mocks.get).toHaveBeenLastCalledWith("/customer-returns/mixed", {
        params: { branch_id: undefined, search: "LOT-2026", page: 1, page_size: 25 },
      }),
    );
    fireEvent.change(screen.getByLabelText("Точка"), { target: { value: zero.branch_id } });
    await waitFor(() =>
      expect(mocks.get).toHaveBeenLastCalledWith("/customer-returns/mixed", {
        params: { branch_id: zero.branch_id, search: "LOT-2026", page: 1, page_size: 25 },
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: /Сбросить всё/ }));
    await waitFor(() =>
      expect(mocks.get).toHaveBeenLastCalledWith("/customer-returns/mixed", {
        params: { branch_id: undefined, search: undefined, page: 1, page_size: 25 },
      }),
    );
  });
  it("hides branch filtering without permission while rows still use the pharmacy timezone", async () => {
    mocks.permissions = ["customer_returns.view"];
    renderPanel();
    await selectMixed();
    expect(screen.queryByLabelText("Точка")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Принять решение" })).not.toBeInTheDocument();
    expect(screen.getAllByText(/15:00/).length).toBeGreaterThan(0);
  });
  it("shows a retryable error without mutations and recovers through GET", async () => {
    let attempts = 0;
    mocks.get.mockImplementation((url: string) => {
      if (url !== "/customer-returns/mixed")
        return Promise.resolve({
          data: { items: [], total: 0, pending: 0, resolved: 0, page: 1, page_size: 25 },
        });
      attempts += 1;
      return attempts === 1
        ? Promise.reject(new Error("Offline"))
        : Promise.resolve({ data: list() });
    });
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: "Смешанные возвраты" }));
    await screen.findByRole("alert");
    fireEvent.click(screen.getByRole("button", { name: "Повторить" }));
    await screen.findByRole("table", { name: "Очередь смешанных возвратов" });
    expect(attempts).toBe(2);
    expect(mocks.post).not.toHaveBeenCalled();
  });
  it("shows loading, empty quarantine and filtered empty states", async () => {
    let resolveList: ((value: { data: ReturnType<typeof list> }) => void) | undefined;
    mocks.get.mockImplementation((url: string) =>
      url === "/customer-returns/mixed"
        ? new Promise<{ data: ReturnType<typeof list> }>((resolve) => {
            resolveList = resolve;
          })
        : Promise.resolve({
            data: { items: [], total: 0, pending: 0, resolved: 0, page: 1, page_size: 25 },
          }),
    );
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: "Смешанные возвраты" }));
    expect(
      await screen.findByRole("status", { name: "Загрузка смешанных возвратов" }),
    ).toBeInTheDocument();
    await waitFor(() => expect(resolveList).toBeDefined());
    resolveList!({ data: list([]) });
    await screen.findByText("Смешанных возвратов в карантине нет.");
    mocks.get.mockResolvedValue({ data: list([]) });
    fireEvent.change(screen.getByLabelText("Поиск"), { target: { value: "нет товара" } });
    await screen.findByText("Возвраты не найдены");
    expect(screen.queryByRole("button", { name: "Принять решение" })).not.toBeInTheDocument();
  });
});

describe("mixed queue response contract", () => {
  it("accepts both kinds and preserves exact decimal strings and historical units", async () => {
    mocks.get.mockResolvedValue({ data: { ...list(), page_size: 200 } });
    const response = await listMixedCustomerReturns({ page_size: 200 });
    expect(response.page_size).toBe(200);
    expect(response.items).toEqual([zero, cash]);
    expect(mocks.get).toHaveBeenLastCalledWith("/customer-returns/mixed", {
      params: { branch_id: undefined, search: undefined, page: 1, page_size: 200 },
    });
  });
  it.each([
    ["numeric packs", { ...zero, legacy_quantity: 0.125 }],
    ["zero packs", { ...zero, legacy_quantity: "0.000" }],
    ["negative packs", { ...zero, legacy_quantity: "-0.125" }],
    ["uncanonical packs", { ...zero, legacy_quantity: "00.125" }],
    ["extra pack precision", { ...zero, legacy_quantity: "0.1250" }],
    ["short pack precision", { ...zero, legacy_quantity: "0.12" }],
    ["pack exponent", { ...zero, legacy_quantity: "1e3" }],
    ["overflow packs", { ...zero, legacy_quantity: "100000000000.000" }],
    ["pack batch missing", { ...zero, batch_id: null }],
    ["pack lot present", { ...zero, lot_id: uuid(90) }],
    ["pack units present", { ...zero, base_units: 1 }],
    ["pack label mismatch", { ...zero, unit_label: "табл." }],
    ["physical lot missing", { ...cash, lot_id: null }],
    ["physical batch present", { ...cash, batch_id: uuid(90) }],
    ["physical packs present", { ...cash, legacy_quantity: "0.125" }],
    ["physical units zero", { ...cash, base_units: 0 }],
    ["physical units negative", { ...cash, base_units: -1 }],
    ["physical units fractional", { ...cash, base_units: 1.5 }],
    ["physical units overflow", { ...cash, base_units: 100000000000 }],
    ["physical unknown unit", { ...cash, unit_label: "штук" }],
    ["unknown inventory kind", { ...zero, inventory_kind: "physical" }],
    ["zero return sale", { ...zero, return_sale_id: uuid(90) }],
    ["zero return receipt", { ...zero, return_receipt_number: "R-001" }],
    ["cash return sale missing", { ...cash, return_sale_id: null }],
    ["cash return receipt missing", { ...cash, return_receipt_number: null }],
    ["resolved status", { ...zero, status: "resolved" }],
    ["unknown intake", { ...zero, intake_kind: "adjustment" }],
    ["unknown reason", { ...zero, refund_reason: "unknown" }],
    ["missing expiry", { ...zero, expires_at: null }],
    ["timezone missing", { ...zero, report_timezone: "unknown/timezone" }],
    ["unaware received datetime", { ...zero, received_at: "2026-10-09T10:00:00" }],
    ["oversized catalog name", { ...zero, catalog_name: "x".repeat(501) }],
    ["private cost field", { ...zero, total_cost_amount: "1.00" }],
  ])("rejects %s", async (_description, item) => {
    mocks.get.mockResolvedValue({ data: { ...list(), items: [item] } });
    await expect(listMixedCustomerReturns({ page: 1 })).rejects.toThrow();
  });
  it.each([
    ["page size overflow", { ...list(), page_size: 201 }],
    ["page overflow", { ...list(), page: 2147483648 }],
    ["negative total", { ...list(), total: -1 }],
    ["unknown envelope field", { ...list(), financial_totals: "private" }],
  ])("rejects %s", async (_description, data) => {
    mocks.get.mockResolvedValue({ data });
    await expect(listMixedCustomerReturns({ page: 1 })).rejects.toThrow();
  });
});
